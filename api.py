# api.py

import asyncio
import aiohttp
import time
import pandas as pd
from c_log import UnifiedLogger
import inspect

logger = UnifiedLogger("api")


class PhemexPublicApi:
    def __init__(self):
        self.base_url = 'https://api.phemex.com'
        self.exchangeInfo_url = f'{self.base_url}/public/products'
        self.klines_url = f'{self.base_url}/exchange/public/md/v2/kline/last'
        self.ticker_v3_url = f'{self.base_url}/md/v3/ticker/24hr/all'

        self.filtered_symbols: set[str] = set()
        self.instruments: dict[str, dict] = {}

        # Лимитер запросов (Rate Limiter) для защиты от бана API
        self._kline_lock = asyncio.Lock()
        self._last_kline_time = 0.0
        self.kline_interval = 0.15  # мин. время между запросами свечей (сек)

    async def update_filtered_symbols(self, session: aiohttp.ClientSession):
        """Получаем список доступных торговых символов PERPETUAL USDT"""
        try:
            async with session.get(self.exchangeInfo_url) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch exchange info: {response.status}")
                    return
                data = await response.json()
                
            root = data.get("data", {})
            arr = root.get("perpProductsV2") or root.get("perpProducts") or []
            
            instruments = {}
            for item in arr:
                if not isinstance(item, dict): continue
                
                sym = str(item.get("symbol", "")).strip()
                quote = str(item.get("quoteCurrency") or item.get("settleCurrency") or "").upper().strip()
                status = str(item.get("status") or item.get("state") or item.get("symbolStatus") or "").strip().lower()
                is_active = not any(word in status for word in ("delist", "suspend", "pause", "settle", "close", "expired"))
                
                if sym and not sym.startswith("s") and quote == "USDT" and is_active:
                    sym_u = sym.upper()
                    # Элегантная распаковка с перезаписью нужных полей
                    instruments[sym_u] = {
                        **item,
                        "symbol": sym_u,
                        "_parsed_price_scale": float(item.get("priceScale", 10000.0))
                    }
                    
            if not instruments: 
                logger.warning("No perpetual USDT symbols found in exchange info")
                return  
                
            self.instruments = instruments
            self.filtered_symbols = set(self.instruments.keys())
        except Exception as ex:
            logger.exception(f"{ex} in {inspect.currentframe().f_code.co_name}")

    def get_precisions(self) -> dict[str, tuple[float, float]]:
        """Возвращает шаг изменения цены (tick size)"""
        precisions = {}
        for sym, item in self.instruments.items():
            raw_tick = item.get("tickSize")
            max_lvg = float(item.get("limitOrderMaxLeverage") or item.get("maxLeverage") or 10.0)
            try:
                precisions[sym] = (float(raw_tick) if raw_tick else 0.0001, max_lvg)
            except:
                precisions[sym] = (0.0001, 10)
        return precisions

    async def get_hot_and_fair_prices(self, session: aiohttp.ClientSession) -> dict[str, dict[str, float]] | None:
        """Возвращает горячие и справедливые цены за один запрос"""
        try:
            async with session.get(self.ticker_v3_url) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch prices: {response.status}")
                    return None
                data = await response.json()
                
            items = data.get("result", [])
            result = {}
            for item in items:
                sym = item.get("symbol")
                if sym in self.filtered_symbols:
                    hot = float(item.get("lastRp", item.get("lastPriceRp", 0)) or 0)
                    fair = float(item.get("markRp", item.get("markPriceRp", 0)) or 0)
                    if hot > 0:
                        result[sym] = {"hot": hot, "fair": fair}
            return result
        except Exception as ex:
            logger.exception(f"{ex} in {inspect.currentframe().f_code.co_name}")
            return None

    async def get_klines_basic(
            self,
            session: aiohttp.ClientSession,
            symbol: str,
            interval: str,
            limit: int):
        """Загружает свечи и возвращает DataFrame с колонкой Close"""
        
        # 1. Защита от спама (Rate Limiting)
        async with self._kline_lock:
            elapsed = time.monotonic() - self._last_kline_time
            if elapsed < self.kline_interval:
                await asyncio.sleep(self.kline_interval - elapsed)
            self._last_kline_time = time.monotonic()

        res_map = {
            "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "4h": 14400, "1d": 86400
        }
        resolution = res_map.get(interval, 60)
        
        # 2. УЗКОЕ ГОРЛЫШКО ИСПРАВЛЕНО: Phemex принимает только строгие значения limit
        allowed_limits = [5, 10, 50, 100, 500, 1000]
        # Ищем минимально подходящий лимит из разрешенных биржей
        valid_limit = next((l for l in allowed_limits if l >= limit), 1000)
        
        params = {
            "symbol": symbol, 
            "resolution": int(resolution), 
            "limit": valid_limit
        }

        try:
            async with session.get(self.klines_url, params=params) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.error(f"Failed klines: {response.status}, {text}, {symbol}")
                    return pd.DataFrame(columns=['Close'])

                data = await response.json()
                
            rows = data.get("data", {}).get("rows", [])
            if not rows: 
                return pd.DataFrame(columns=['Close'])

            # ОПРЕДЕЛЯЕМ МАСШТАБ (SCALE) БЕЗОПАСНО
            inst = self.instruments.get(symbol, {})
            scale = inst.get("_parsed_price_scale", 0)
            
            if scale <= 0:
                tick = float(inst.get("tickSize", 0.0001))
                scale = 1 / tick if tick > 0 else 10000.0

            parsed_data = []
            for r in rows:
                if len(r) >= 7:
                    # r[0] - timestamp, r[6] - close (в формате Ep)
                    parsed_data.append([int(r[0]), float(r[6]) / scale])

            df = pd.DataFrame(parsed_data, columns=['Time', 'Close'])
            df['Time'] = pd.to_datetime(df['Time'], unit='s')
            df.set_index('Time', inplace=True)
            
            # 3. ФИКС РЕВЕРСА: Сортируем время от прошлого к настоящему!
            df.sort_index(inplace=True)
            
            return df

        except Exception as ex:
            logger.exception(f"{ex} in {inspect.currentframe().f_code.co_name}")
            return pd.DataFrame(columns=['Close'])



# # ============================================================
# # SELF TEST
# # ============================================================
# if __name__ == "__main__":
#     async def _main():
#         import logging
#         global logger
#         logger = logging.getLogger("api_test")
#         logging.basicConfig(level=logging.INFO)
        
#         api = PhemexPublicApi()
        
#         async with aiohttp.ClientSession() as session:
#             print("1. Обновляем символы...")
#             await api.update_filtered_symbols(session)
            
#             symbols = list(api.filtered_symbols)
#             if not symbols: return
            
#             test_sym = "u1000SHIBUSDT" if "u1000SHIBUSDT" in api.filtered_symbols else symbols[0]
            
#             print(f"\n2. Тест свечей для {test_sym} (теперь через /kline/last):")
#             df = await api.get_klines_basic(session, test_sym, "1m", 5)
#             print(df)

#             print("\n3. Сбор сводки по 20 монетам (Цены + Округление):")
#             prices = await api.get_hot_and_fair_prices(session)
#             precisions = api.get_precisions()
            
#             summary = []
#             for sym in symbols[:20]:
#                 p_data = prices.get(sym, {"hot": 0, "fair": 0})
#                 tick = precisions.get(sym, 0)
                
#                 # Демонстрация округления (берем горячую цену и "грязное" число)
#                 raw_val = p_data['hot'] * 1.00012345
#                 rounded = round(raw_val / tick) * tick if tick > 0 else raw_val
                
#                 summary.append({
#                     "Symbol": sym,
#                     "Hot": p_data['hot'],
#                     "Fair": p_data['fair'],
#                     "Tick": tick,
#                     "Test_Round": f"{rounded:.8g}"
#                 })
            
#             print(pd.DataFrame(summary).to_string(index=False))
            
#     asyncio.run(_main())