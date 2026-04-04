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
        self.signals_cache: Dict[str, Tuple[float, float, bool]] = {}  
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

            diff_percent = (fair_price - last_price) / last_price * 100            

            # Извлекаем данные из кэша
            signal_data = self.signals_cache.get(symbol)
            if signal_data:
                record_time, _, is_sent = signal_data
                diff_time = now - record_time
                in_signal = True
            else:
                record_time = now
                is_sent = False
                diff_time = 0
                in_signal = False

            # Удаляем "протухшие" сигналы из кэша (сброс кулдауна)
            if in_signal and diff_time > self.flush_ttl:
                self.signals_cache.pop(symbol, None)
                in_signal = False
                record_time = now
                diff_time = 0
                is_sent = False

            # --- основное условие ---
            if diff_percent >= self.diff_pct: 
                if not in_signal:
                    self.signals_cache[symbol] = (record_time, diff_percent, is_sent)
                    
                if diff_time >= self.ttl and not is_sent:
                    confirmed_signals.append((symbol, diff_percent))
                    self.signals_cache[symbol] = (record_time, diff_percent, True)
            else:
                if in_signal: 
                    self.signals_cache.pop(symbol, None)

        return confirmed_signals
    
    
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
            elif now - self._cache[symbol] >= self.ttl:
                self._valid.add(symbol)
        else:
            self._cache.pop(symbol, None)
            self._valid.discard(symbol)

    def is_valid(self, symbol: str) -> bool:
        if not self.enabled:
            return True
        return symbol in self._valid
    

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