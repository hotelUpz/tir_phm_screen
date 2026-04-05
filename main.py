import asyncio
import aiohttp
import time
from typing import *
import traceback
import pandas as pd

from consts import (
    MAIN_FREQUENTCY,
    SYMBOLS_FREQUENCY,
    SIGNAL_FREQUENCY,
    MIN_LEVERAGE,
    TREND_PATTERN,
    TG_ENABLED,
    STAKAN_PATTERN
)

from api import PhemexPublicApi
from api_ws import PhemexStakanStream, DepthTop
from tg_notifier import TelegramNotifier, Formatter
from d_signal import FairSignalDetector, StakanDetector, TrendConfirmSignal
from c_log import UnifiedLogger

logger = UnifiedLogger("core")


class Core:
    def __init__(self):
        self.stop_bot = False
        self.symbols_state_event = asyncio.Event()
        
        self.phm_public = PhemexPublicApi()
        
        self.signal_detector = FairSignalDetector()
        self.stakan_detector = StakanDetector()
        self.signal_confirm = TrendConfirmSignal()
        
        self.notifier = TelegramNotifier(stop_bot=self.stop_bot)

        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        
        self.bg_tasks: set[asyncio.Task] = set()
        
        # Переменные для WS стрима стакана
        self.stakan_stream: Optional[PhemexStakanStream] = None
        self.stakan_task: Optional[asyncio.Task] = None
        
        # 🛡 КЭШ СВЕЧЕЙ (Защита API и ускорение расчетов)
        self.klines_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
        self.klines_cache_ttl = 60.0  # Храним скачанные свечи 1 минуту

    async def get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                logger.warning("🔄 Инициализация aiohttp сессии...")
                connector = aiohttp.TCPConnector(enable_cleanup_closed=True, limit=50)
                timeout = aiohttp.ClientTimeout(total=15) 
                self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            return self._session

    async def close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def on_depth_update(self, d: DepthTop):
        """Коллбэк для WebSocket. Кормит StakanDetector сырыми данными."""
        self.stakan_detector.update(d.symbol, d.bids, d.asks)

    async def symbols_state_updater(self):
        while not self.stop_bot:
            try:
                session = await self.get_session()
                await self.phm_public.update_filtered_symbols(session)
            except Exception as e:
                logger.error(f"❌ Ошибка обновления символов: {e}")
                await self.close_session()
            finally:
                self.symbols_state_event.set()
                
                # Управление потоком WebSocket (перезапуск, если обновились символы)
                current_symbols = set(self.phm_public.filtered_symbols)
                if current_symbols:
                    if self.stakan_stream is None:
                        logger.info(f"🌐 Инициализация WS стакана для {len(current_symbols)} пар...")
                        self.stakan_stream = PhemexStakanStream(
                            symbols=current_symbols,
                            depth=STAKAN_PATTERN.get("depth", 5),
                            chunk_size=40
                        )
                        self.stakan_task = asyncio.create_task(self.stakan_stream.run(self.on_depth_update))
                        self.bg_tasks.add(self.stakan_task)
                        self.stakan_task.add_done_callback(self.bg_tasks.discard)
                    else:
                        # old_symbols = set(self.stakan_stream.symbols)
                        # current_ws_symbols = {s.upper().strip() for s in current_symbols}
                        # added = current_ws_symbols - old_symbols
                        # removed = old_symbols - current_ws_symbols

                        old_symbols = set(self.stakan_stream.symbols)
                        added = current_symbols - old_symbols
                        removed = old_symbols - current_symbols
                        
                        # Перезапускаем ТОЛЬКО если появились реально новые монеты
                        if added:
                            logger.info(f"🔄 Перезапуск WS стакана: добавлено {len(added)} новых монет. (Пропало: {len(removed)})")
                            self.stakan_stream.stop()
                            if self.stakan_task:
                                await self.stakan_task
                            
                            logger.info(f"🌐 Ре-Инициализация WS стакана для {len(current_symbols)} пар...")
                            self.stakan_stream = PhemexStakanStream(
                                symbols=current_symbols,
                                depth=STAKAN_PATTERN.get("depth", 5),
                                chunk_size=40
                            )
                            self.stakan_task = asyncio.create_task(self.stakan_stream.run(self.on_depth_update))
                            self.bg_tasks.add(self.stakan_task)
                            self.stakan_task.add_done_callback(self.bg_tasks.discard)
                        elif removed:
                            # Если монеты ушли в делинг/техработы — не рвём коннект ради них
                            logger.debug(f"ℹ️ С биржи временно пропало {len(removed)} монет. WS продолжает работу без перезапуска.")

                await asyncio.sleep(SYMBOLS_FREQUENCY)

    async def process_signals(self):
        now = time.time()
        session = await self.get_session()
        
        # 🧹 Очистка старых свечей из кэша (Защита от утечки памяти)
        keys_to_del = [sym for sym, (ts, _) in self.klines_cache.items() if now - ts > self.klines_cache_ttl]
        for k in keys_to_del:
            del self.klines_cache[k]
        
        try:
            price_data = await self.phm_public.get_hot_and_fair_prices(session)
        except Exception as e:
            logger.error(f"❌ Ошибка получения цен: {e}")
            await self.close_session() 
            return

        if not price_data: return   

        precisions = self.phm_public.get_precisions()
        if not precisions:
            logger.warning(f"❌ Не удается получить precisions.")
            return     

        signals = await self.signal_detector.check(price_data, precisions)
        if not signals: return

        valid_signals = []

        for signal_symbol, diff_percent in signals:

            # 1. Получаем точность и плечо (с безопасным дефолтом!)
            prec, max_lvg = precisions.get(signal_symbol, (0.0001, 10.0))
            
            # 2. ФИЛЬТР ПЛЕЧА (скипаем всё, что от 1 до 10)
            # Конфиг можно тянуть из consts, например LEVERAGE_SKIP_RANGE = (1, 10)
            if MIN_LEVERAGE is not None and max_lvg <= MIN_LEVERAGE:
                logger.debug(f"📉 Скип по плечу для {signal_symbol}. {max_lvg} <= {MIN_LEVERAGE}")
                continue

            # 3. Проверка СТАКАНА
            if not self.stakan_detector.is_valid(signal_symbol):
                # logger.debug(f"📉 Стакан НЕ подтверждён для {signal_symbol}. Пропускаем.")
                continue

            # 4. Проверка ТРЕНДА (С использованием КЭША свечей)
            try:
                if signal_symbol in self.klines_cache:
                    klines = self.klines_cache[signal_symbol][1]
                else:
                    klines = await self.phm_public.get_klines_basic(
                        session=session,
                        symbol=signal_symbol,
                        interval=self.signal_confirm.tf,
                        limit=int(self.signal_confirm.slow * 1.5),
                    )
                    # if not klines.empty:
                    #     # Сохраняем скачанный DataFrame в кэш
                    self.klines_cache[signal_symbol] = (now, klines)

            except Exception as e:
                logger.error(f"❌ Ошибка загрузки свечей для {signal_symbol}: {e}")
                continue 

            trend = self.signal_confirm.detect_trend(klines, signal_symbol)
            if trend != "UP":
                # logger.debug(f"📈 Тренд НЕ подтверждён для {signal_symbol}. Пропускаем.")
                continue
            
            stakan_msg = "OK" if STAKAN_PATTERN.get("enable") else "N/A"
            trend_msg = trend if TREND_PATTERN.get(self.signal_confirm.tf, {}).get("enable") else "N/A"
            last_price = price_data.get(signal_symbol, {}).get("hot", 0)
            fair_price = price_data.get(signal_symbol, {}).get("fair", 0)

            valid_signals.append({
                "symbol": signal_symbol,
                "last_price": last_price,
                "fair_price": fair_price,
                "diff_percent": round(diff_percent, 2),
                "price_precision": prec,
                "stakan_msg": stakan_msg,
                "trend_msg": trend_msg
            })
            
            # ✅ ФИКС: Подтверждаем отправку (отправляем в бан_кэш)
            self.signal_detector.confirm_sent(signal_symbol)
            logger.info(f"✅ Готов сигнал по монете {signal_symbol}.")

        if valid_signals and TG_ENABLED:
            report_text = Formatter.format_coins_for_tg(valid_signals)
            if report_text:
                task = asyncio.create_task(self.notifier.send(text=report_text))
                self.bg_tasks.add(task)
                task.add_done_callback(self.bg_tasks.discard)

    async def _run(self):
        logger.info("[INFO] ✨ Скринер начал работу.")
        self.updater_task = asyncio.create_task(self.symbols_state_updater())        
        
        try:
            await asyncio.wait_for(self.symbols_state_event.wait(), timeout=30.0)
            logger.info("Символы загружены")
        except asyncio.TimeoutError:
            logger.error("Таймаут загрузки символов — продолжаем (обновятся в фоне)")

        signal_updating_time = time.monotonic()

        while not self.stop_bot:
            try:
                now = time.monotonic()
                if now - signal_updating_time >= SIGNAL_FREQUENCY:
                    signal_updating_time = now
                    await self.process_signals()

            except asyncio.CancelledError:
                break
            except Exception as ex:
                tb = traceback.format_exc()
                logger.exception(f"ОШИБКА в основном цикле: {ex}\n{tb}")

            await asyncio.sleep(MAIN_FREQUENTCY)

    async def shutdown(self):
        logger.info("Остановка скринера, закрытие сессий и задач...")
        self.stop_bot = True
        self.notifier.stop_bot = True

        if self.stakan_stream:
            self.stakan_stream.stop()

        if hasattr(self, 'updater_task') and not self.updater_task.done():
            self.updater_task.cancel()
            try: await self.updater_task
            except asyncio.CancelledError: pass

        if self.bg_tasks:
            logger.info(f"Ожидание завершения {len(self.bg_tasks)} фоновых задач...")
            await asyncio.gather(*self.bg_tasks, return_exceptions=True)

        await self.close_session()
        logger.info("Все процессы корректно завершены.")

async def main():
    instance = Core()
    try: await instance._run()
    except asyncio.CancelledError: print("🚩 Асинхронная задача была отменена.")
    except KeyboardInterrupt: print("\n⛔ Остановка по Ctrl+C")
    finally: await instance.shutdown()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass


# chmod 600 ssh_key.txt
# chmod 600 ssh_key.pub
# chmod 600 .ssh-autostart.sh
# eval "$(ssh-agent -s)"
# ssh-add ssh_key.txt
# source .ssh-autostart.sh
# git push --set-upstream origin master
# git config --global push.autoSetupRemote true
# ssh -T git@github.com 
# git log -1

# git add .
# git commit -m "plh37"
# git push

# pip install anthropic
# npm install -g @anthropic-ai/claude-code

# export ANTHROPIC_API_KEY=...
# claude

# python -m pip install --upgrade pip
# pip install -r requirements.txt