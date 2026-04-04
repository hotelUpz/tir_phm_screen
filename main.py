# main.py

import asyncio
import aiohttp
import time
from typing import *
import traceback

from consts import MAIN_FREQUENTCY, SYMBOLS_FREQUENCY, SIGNAL_FREQUENCY, TREND_LINE, TG_ENABLED

from api import PhemexPublicApi
from tg_notifier import TelegramNotifier, Formatter
from d_signal import FairSignalDetector, TrendConfirmSignal
from c_log import UnifiedLogger

logger = UnifiedLogger("core")


class Core:
    def __init__(self):
        self.stop_bot = False
        self.symbols_state_event = asyncio.Event()
        
        self.phm_public = PhemexPublicApi()
        self.signal_detector = FairSignalDetector()
        self.signal_confirm = TrendConfirmSignal()
        self.notifier = TelegramNotifier(stop_bot=self.stop_bot)

        # Переменные для контроля сессии
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        
        # Хранилище фоновых задач (чтобы их не удалял сборщик мусора)
        self.bg_tasks: set[asyncio.Task] = set()

    async def get_session(self) -> aiohttp.ClientSession:
        """
        Самовосстанавливающийся коннектор.
        Проверяет жива ли сессия, и если нет — пересоздает её.
        """
        async with self._session_lock:
            if self._session is None or self._session.closed:
                logger.warning("🔄 Инициализация/перезапуск aiohttp сессии...")
                connector = aiohttp.TCPConnector(enable_cleanup_closed=True, limit=50)
                # Таймаут на всю сессию защитит от зависаний при падении провайдера
                timeout = aiohttp.ClientTimeout(total=15) 
                self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            return self._session

    async def close_session(self):
        """Принудительно закрывает сессию для сброса соединения"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def symbols_state_updater(self):
        while not self.stop_bot:
            try:
                session = await self.get_session()
                await self.phm_public.update_filtered_symbols(session)
            except Exception as e:
                logger.error(f"❌ Ошибка обновления символов (упала сеть?): {e}")
                # Форсируем реконнект при следующей итерации
                await self.close_session()
            finally:
                self.symbols_state_event.set()
                await asyncio.sleep(SYMBOLS_FREQUENCY)

    async def process_signals(self):
        session = await self.get_session()
        
        try:
            price_data = await self.phm_public.get_hot_and_fair_prices(session)
        except Exception as e:
            logger.error(f"❌ Ошибка получения цен: {e}")
            await self.close_session()  # Сбрасываем сессию при обрыве
            return

        if not price_data:
            return

        # Получаем список подтвержденных сигналов
        signals = await self.signal_detector.check(price_data)
        if not signals:
            return

        valid_signals = []
        precisions = self.phm_public.get_precisions()

        for signal_symbol, diff_percent in signals:
            try:
                # Сбор свечей и проверка тренда
                klines = await self.phm_public.get_klines_basic(
                    session=session,
                    symbol=signal_symbol,
                    interval=self.signal_confirm.tf,
                    limit=int(self.signal_confirm.slow * 1.5),
                )
            except Exception as e:
                logger.error(f"❌ Ошибка загрузки свечей для {signal_symbol}: {e}")
                continue # Пропускаем монету, но не роняем весь цикл

            trend = self.signal_confirm.detect_trend(klines, signal_symbol)
            if trend != "UP":
                logger.debug(f"📈 Тренд НЕ подтверждён для {signal_symbol}. Пропускаем.")
                continue

            trend_msg = trend if TREND_LINE.get(self.signal_confirm.tf, {}).get("enable") else "N/A"

            last_price = price_data.get(signal_symbol, {}).get("hot", 0)
            fair_price = price_data.get(signal_symbol, {}).get("fair", 0)
            prec = precisions.get(signal_symbol, 0.0001)

            valid_signals.append({
                "symbol": signal_symbol,
                "last_price": last_price,
                "fair_price": fair_price,
                "diff_percent": round(diff_percent, 2),
                "price_precision": prec,
                "trend_msg": trend_msg
            })
            logger.info(f"✅ Готов сигнал по монете {signal_symbol}.")

        # Массовая отправка
        if valid_signals and TG_ENABLED:
            report_text = Formatter.format_coins_for_tg(valid_signals)
            if report_text:
                # Грамотный запуск и трекинг фоновых задач отправки
                task = asyncio.create_task(self.notifier.send(text=report_text))
                self.bg_tasks.add(task)
                task.add_done_callback(self.bg_tasks.discard)

    async def _run(self):
        logger.info("[INFO] ✨ Скринер начал работу.")

        # Сохраняем таску апдейтера как атрибут для корректного закрытия
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
                
                # Блок обработки сигналов
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
        """Мягко завершает все запущенные процессы и сессии"""
        logger.info("Остановка скринера, закрытие сессий и задач...")
        self.stop_bot = True
        self.notifier.stop_bot = True

        # Отменяем таску обновления символов
        if hasattr(self, 'updater_task') and not self.updater_task.done():
            self.updater_task.cancel()
            try:
                await self.updater_task
            except asyncio.CancelledError:
                pass

        # Дожидаемся завершения отправки уже подготовленных сообщений в ТГ
        if self.bg_tasks:
            logger.info(f"Ожидание завершения {len(self.bg_tasks)} фоновых задач...")
            await asyncio.gather(*self.bg_tasks, return_exceptions=True)

        # Закрываем сессию
        await self.close_session()
        logger.info("Все процессы корректно завершены.")


async def main():
    instance = Core()
    try:
        await instance._run()
    except asyncio.CancelledError:
        print("🚩 Асинхронная задача была отменена.")
    except KeyboardInterrupt:
        print("\n⛔ Остановка по Ctrl+C")
    finally:
        await instance.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


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