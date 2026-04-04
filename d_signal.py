import time
import pandas as pd
from typing import Optional, Dict, List, Tuple
from consts import DIFF_PCT, SIGNAL_TTL, FLUSH_SIGNAL_TTL, TREND_LINE, BLACK_SET


class FairSignalDetector:
    """
    Отслеживает расхождение справедливой и горячей цены.
    Возвращает СПИСОК всех созревших сигналов.
    """
    def __init__(self):
        self.signals_cache: Dict[str, Tuple[float, float]] = {}  # {symbol: (first_time_sec, diff_pct)}
        self.diff_pct = abs(DIFF_PCT) if DIFF_PCT else 5.0
        self.ttl = SIGNAL_TTL or 60
        self.flush_ttl = FLUSH_SIGNAL_TTL or 300

    async def check(
        self,
        price_data: Dict[str, dict[str, float]],
    ) -> List[Tuple[str, float]]:
        """
        Проверяет все символы и возвращает список подтвержденных сигналов.
        """
        now = time.time()  # Держим в секундах, т.к. константы в секундах
        confirmed_signals = []

        for symbol, prices in price_data.items():
            if symbol in BLACK_SET:
                continue
            
            last_price = prices.get("hot")
            fair_price = prices.get("fair")
            if not last_price or not fair_price:
                continue

            diff_percent = (fair_price - last_price) / last_price * 100            

            signal_data = self.signals_cache.get(symbol)
            record_time = signal_data[0] if signal_data else 0
            diff_time = now - record_time if record_time else 0
            in_signal = symbol in self.signals_cache

            # Удаляем "протухшие" сигналы из кэша
            if in_signal and diff_time > self.flush_ttl:
                self.signals_cache.pop(symbol, None)
                continue

            # --- основное условие ---
            if diff_percent >= self.diff_pct: 
                if not in_signal:
                    # Первый фикс сигнала
                    self.signals_cache[symbol] = (now, diff_percent)
                else:
                    # Подтверждение по TTL
                    if diff_time >= self.ttl:
                        confirmed_signals.append((symbol, diff_percent))
                        # Удаляем из кэша, чтобы сигнал улетел и начал цикл заново (без спама)
                        self.signals_cache.pop(symbol, None)
            else:
                # Условие не удерживается — сброс таймера
                if in_signal: 
                    self.signals_cache.pop(symbol, None)

        return confirmed_signals


class TrendConfirmSignal:
    """
    Проверяет направление тренда по короткой и длинной EMA.
    (Использует встроенный Pandas EWM, без тяжеловесного pandas_ta)
    """
    def __init__(self, trend_config: dict = None):
        self.trend_cfg = trend_config or TREND_LINE
        self.tf = next(iter(self.trend_cfg.keys()), "5m")

        cfg = self.trend_cfg.get(self.tf, {})
        self.enabled = cfg.get("enable", False)
        self.fast = cfg.get("fast", 10)
        self.slow = cfg.get("slow", 30)

    def detect_trend(self, df: pd.DataFrame) -> Optional[str]:
        if not self.enabled:
            return "UP"

        if df.empty or 'Close' not in df.columns or len(df) < self.slow:
            return None

        df = df.copy()
        
        # Нативный расчет EMA через Pandas 
        df['ema_fast'] = df['Close'].ewm(span=self.fast, adjust=False).mean()
        df['ema_slow'] = df['Close'].ewm(span=self.slow, adjust=False).mean()

        last_row = df.iloc[-1]
        fast_val, slow_val = last_row['ema_fast'], last_row['ema_slow']

        if pd.isna(fast_val) or pd.isna(slow_val):
            return None

        if fast_val > slow_val:
            return "UP"
        elif fast_val <= slow_val:
            return "DOWN"
        return None