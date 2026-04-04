import os 
from dotenv import load_dotenv

load_dotenv()

# ____________________
# SIGNAL:
# Надо везде поменять так как теперь изменился доступ
# base signal
# DIFF_PCT:         float = 1.0           # % отклонения справедливой цены от горячей. fear > hot
# SIGNAL_TTL:       float = 0.1 * 60      # 1 минутa. Если сигнал продержался в течение этого времени то его считать подтвержденным.

HOT_FAIR_PATTERN = {
    "spread": 1.0,       # % отклонения справедливой цены от горячей. fear > hot
    "ttl": 0.1 * 60,     # 1 минутa. Если сигнал продержался в течение этого времени то его считать подтвержденным.
    "flush_ttl": 5 * 60  # 1 минут. Время жизни сигнала поистечение которого он сбрасывается из кеша сигналов.

}

# add signal
STAKAN_PATTERN = {
    "enable": True,
    "depth": 5,
    "ask1_bid1_max_spread": 1.0, # максимально допустимое расстояние между бидом и аском
    "ttl": 2.0                   # sec. выдержка паттерна стакана
}

TREND_PATTERN: dict = {
    "5m": {
        "enable": True,   # включен ли индикатор
        "fast": 10,       # длина короткой волны EMA       
        "slow": 30        # длина длинной волны EMA  
    }
}


FLUSH_SIGNAL_TTL: float = 5 * 60  # 5 минут. Время жизни сигнала поистечение которого он сбрасывается из кеша сигналов. -- надо вынести глобально а не только для ценового спреда.


MAIN_FREQUENTCY: float = 0.1 # sec
SIGNAL_FREQUENCY: float = 1 # sec
SYMBOLS_FREQUENCY: float = 300 # sec

BLACK_SET: set = set()     # черный список монет. Формат монеты: "BTCUSDT" и т.д.

TG_ENABLED: bool = True
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
CHAT_IDS = [os.getenv("CHAT_ID_1"), ]
MIN_SEND_INTERVAL: float = 0.5 # sec

PRECISION: int = 20

LOG_DEBUG = True
LOG_ERROR = True
LOG_INFO = True 
LOG_WARNING = True
MAX_LOG_LINES = 1000 
TIME_ZONE = "UTC"