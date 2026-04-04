from __future__ import annotations

import time
import pandas as pd
from typing import Optional, Literal, Any, List, Dict, List, Tuple
from consts import HOT_FAIR_PATTERN, FLUSH_SIGNAL_TTL, TREND_PATTERN, BLACK_SET, STAKAN_PATTERN
from c_log import UnifiedLogger

logger = UnifiedLogger("signal")


class FairSignalDetector:
    """
    Отслеживает расхождение справедливой и горячей цены.
    Возвращает СПИСОК всех созревших сигналов.
    """
    def __init__(self):
        # Добавили флаг is_sent -> {symbol: (first_time_sec, diff_pct, is_sent)}
        self.signals_cache: Dict[str, Tuple[float, float, bool]] = {}  
        self.diff_pct = abs(HOT_FAIR_PATTERN.get("spread")) or 5.0
        self.ttl = HOT_FAIR_PATTERN.get("ttl") or 60
        self.flush_ttl = FLUSH_SIGNAL_TTL or 300

    async def check(
        self,
        price_data: Dict[str, dict[str, float]],
    ) -> List[Tuple[str, float]]:
        """
        Проверяет все символы и возвращает список подтвержденных сигналов.
        """
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
            record_time = signal_data[0] if signal_data else 0
            is_sent = signal_data[2] if signal_data else False
            
            diff_time = now - record_time if record_time else 0
            in_signal = symbol in self.signals_cache

            # Удаляем "протухшие" сигналы из кэша (это наш кулдаун)
            if in_signal and diff_time > self.flush_ttl:
                self.signals_cache.pop(symbol, None)
                continue

            # --- основное условие ---
            if diff_percent >= self.diff_pct: 
                if not in_signal:
                    # Первый фикс сигнала: запоминаем время и ставим is_sent = False
                    self.signals_cache[symbol] = (now, diff_percent, False)
                    
                # Подтверждение по TTL (если еще не отправляли)
                if diff_time >= self.ttl and not is_sent:
                    confirmed_signals.append((symbol, diff_percent))
                    # МАРКИРУЕМ КАК ОТПРАВЛЕННЫЙ, НО ОСТАВЛЯЕМ В КЭШЕ!
                    self.signals_cache[symbol] = (record_time, diff_percent, True)
            else:
                # Условие не удерживается — сброс таймера
                if in_signal: 
                    self.signals_cache.pop(symbol, None)

        return confirmed_signals
    
    
class StakanDetector:
    def __init__(self):
        self.cfg = STAKAN_PATTERN
        self.enabled: bool = self.cfg.get("enable", True)
        self.depth: int = self.cfg.get("depth", 5)
        self.ask1_bid1_max_spread: Optional[float] = self.cfg.get("ask1_bid1_max_spread")
        self.ttl = self.cfg.get("ttl") or 1.0

    def check_pattern(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> bool:
        if not self.enabled:
            return True 

        if len(bids) < self.depth or len(asks) < self.depth:
            return None
        
        ask1 = asks[0][0]
        bids1 = bids[0][0]
        spread = abs(ask1 - bids1) / ask1 * 100

        return spread <= self.ask1_bid1_max_spread # self.ttl надо граматно выдержать это время (в секундах)
    

class TrendConfirmSignal:
    """
    Проверяет направление тренда по короткой и длинной EMA.
    (Использует встроенный Pandas EWM, без тяжеловесного pandas_ta)
    """
    def __init__(self):
        self.trend_cfg = TREND_PATTERN
        self.tf = next(iter(self.trend_cfg.keys()), "5m")

        cfg = self.trend_cfg.get(self.tf, {})
        self.enabled = cfg.get("enable", False)
        self.fast = cfg.get("fast", 10)
        self.slow = cfg.get("slow", 30)

    def detect_trend(self, df: pd.DataFrame, symbol: str = "UNKNOWN") -> Optional[str]:
        # Обрати внимание: добавил аргумент symbol для красивого логирования
        if not self.enabled:
            return "UP"

        if df.empty or 'Close' not in df.columns or len(df) < self.slow:
            logger.debug(f"{symbol}: df data is corrupt")
            return None

        df = df.copy()
        
        # Нативный расчет EMA через Pandas 
        df['ema_fast'] = df['Close'].ewm(span=self.fast, adjust=False).mean()
        df['ema_slow'] = df['Close'].ewm(span=self.slow, adjust=False).mean()

        last_row = df.iloc[-1] # Теперь это точно ПОСЛЕДНЯЯ (текущая) свеча
        fast_val, slow_val = last_row['ema_fast'], last_row['ema_slow']
        current_price = last_row['Close']

        # Логируем значения, чтобы сравнивать с графиком биржи
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