import time
import pandas as pd
from typing import Optional, Dict, List, Tuple
from consts import HOT_FAIR_PATTERN, STAKAN_PATTERN, TREND_PATTERN, BLACK_SET
from c_log import UnifiedLogger

logger = UnifiedLogger("signal")


import time
import pandas as pd
from typing import Optional, Dict, List, Tuple
from consts import HOT_FAIR_PATTERN, STAKAN_PATTERN, TREND_PATTERN, BLACK_SET
from c_log import UnifiedLogger

logger = UnifiedLogger("signal")


class FairSignalDetector:
    """
    Отслеживает расхождение справедливой и горячей цены.
    Возвращает СПИСОК всех созревших сигналов.
    """
    def __init__(self):
        # Хранит время старта накопления сигнала: {symbol: start_time}
        self.signals_cache: Dict[str, float] = {}  
        # Хранит время начала бана: {symbol: ban_start_time}
        self.ban_cache: Dict[str, float] = {}      
        
        self.diff_pct = abs(HOT_FAIR_PATTERN.get("spread", 1.0))
        self.ttl = HOT_FAIR_PATTERN.get("ttl", 6.0)
        self.flush_ttl = HOT_FAIR_PATTERN.get("flush_ttl", 300.0)

    async def check(
        self,
        price_data: Dict[str, dict[str, float]],
    ) -> List[Tuple[str, float]]:
        now = time.time()  
        confirmed_signals = []

        for symbol, prices in price_data.items():
            if symbol in BLACK_SET:
                continue
            
            last_price = prices.get("hot")
            fair_price = prices.get("fair")
            if not last_price or not fair_price:
                continue

            # 1. Проверяем БАН-ЛИСТ (независимо от текущей цены!)
            if symbol in self.ban_cache:
                if now - self.ban_cache[symbol] > self.flush_ttl:
                    # Срок бана вышел — амнистия
                    del self.ban_cache[symbol]
                else:
                    # 🛑 КРИТИЧНО: Если монета в бане, удаляем из кэша и делаем continue!
                    # Иначе она пойдет ниже, снова наберет TTL и будет долбить ядро.
                    self.signals_cache.pop(symbol, None)
                    continue

            # 2. Основная логика удержания сигнала
            diff_percent = (fair_price - last_price) / last_price * 100            

            if diff_percent >= self.diff_pct: 
                # Фиксируем время старта, если монеты еще нет в кэше
                if symbol not in self.signals_cache:
                    self.signals_cache[symbol] = now
                    
                # Если удержали ttl — отдаем ядру
                if now - self.signals_cache[symbol] >= self.ttl:
                    confirmed_signals.append((symbol, diff_percent))
                    # Внимание: мы не баним монету здесь! Ждем команды от ядра.
            else:
                # Цена упала — сбрасываем накопление времени
                self.signals_cache.pop(symbol, None)

        return confirmed_signals
    
    def confirm_sent(self, symbol: str):
        """
        Вызывается ядром ТОЛЬКО ПОСЛЕ того, как сигнал прошел стакан, тренд и улетел в ТГ.
        Вешает железобетонный бан на flush_ttl секунд.
        """
        self.ban_cache[symbol] = time.time()
        self.signals_cache.pop(symbol, None)
    
    
class StakanDetector:
    """
    Фоновый детектор стакана. Обновляется через WS, хранит готовые к торгам символы.
    """
    def __init__(self):
        self.cfg = STAKAN_PATTERN
        self.enabled: bool = self.cfg.get("enable", True)
        self.depth: int = self.cfg.get("depth", 5)
        self.max_spread: float = self.cfg.get("ask1_bid1_max_spread", 1.0)
        self.ttl: float = self.cfg.get("ttl", 1.0)
        
        self._cache: Dict[str, float] = {}
        self._valid: set[str] = set()

    def update(self, symbol: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]):
        if not self.enabled:
            return 

        if len(bids) < 1 or len(asks) < 1:
            self._cache.pop(symbol, None)
            self._valid.discard(symbol)
            return
        
        ask1 = asks[0][0]
        bid1 = bids[0][0]
        if ask1 <= 0: 
            return

        spread = abs(ask1 - bid1) / ask1 * 100
        now = time.time()

        if spread <= self.max_spread:
            if symbol not in self._cache:
                self._cache[symbol] = now
            if now - self._cache[symbol] >= self.ttl:
                self._valid.add(symbol)
        else:
            self._cache.pop(symbol, None)
            self._valid.discard(symbol)

    # def is_valid(self, symbol: str) -> bool:
    #     if not self.enabled:
    #         return True
    #     return symbol in self._valid

    def is_valid(self, symbol: str) -> bool:
        if not self.enabled:
            return True
        if symbol in self._valid:
            return True
            
        # ✅ ФИКС: Динамическая проверка "на лету", если WS молчит
        if symbol in self._cache:
            if time.time() - self._cache[symbol] >= self.ttl:
                self._valid.add(symbol)
                return True
                
        return False
    

class TrendConfirmSignal:
    def __init__(self):
        self.trend_cfg = TREND_PATTERN
        self.tf = next(iter(self.trend_cfg.keys()), "5m")

        cfg = self.trend_cfg.get(self.tf, {})
        self.enabled = cfg.get("enable", False)
        self.fast = cfg.get("fast", 10)
        self.slow = cfg.get("slow", 30)

    def detect_trend(self, df: pd.DataFrame, symbol: str = "UNKNOWN") -> Optional[str]:
        if not self.enabled:
            return "UP"

        if df.empty or 'Close' not in df.columns or len(df) < self.slow:
            logger.debug(f"{symbol}: df data is corrupt or not enough klines")
            return None

        df = df.copy()
        
        df['ema_fast'] = df['Close'].ewm(span=self.fast, adjust=False).mean()
        df['ema_slow'] = df['Close'].ewm(span=self.slow, adjust=False).mean()

        last_row = df.iloc[-1] 
        fast_val, slow_val = last_row['ema_fast'], last_row['ema_slow']
        current_price = last_row['Close']

        logger.debug(
            f"📊 [{symbol}] Price: {current_price:.5g} | "
            f"EMA({self.fast}): {fast_val:.5g} | EMA({self.slow}): {slow_val:.5g}"
        )

        if pd.isna(fast_val) or pd.isna(slow_val):
            return None

        if fast_val > slow_val:
            return "UP"
        elif fast_val <= slow_val:
            return "DOWN"
        return None