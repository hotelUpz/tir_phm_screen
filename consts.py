import os 
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ____________________
# SIGNAL:
HOT_FAIR_PATTERN = {
    "lever_dependencies": {
        (20, 40): {
            "spread": 5.0,      # % отклонения справедливой цены от горячей. fear > hot
            "ttl": 10,          # sec. Если сигнал продержался в течение этого времени то его считать подтвержденным.
        },
        (41, 70): {
            "spread": 2.5,       
            "ttl": 10,     
        },
        (71, 500): {
            "spread": 1.5,       
            "ttl": 10,    
        },
    }

}

# add signal
MIN_LEVERAGE: Optional[int] = 20 # None -- откл проверку. Если максимально допустимое плечо монеты <= MIN_LEVERAGE, то сигнал по ней скипается.

STAKAN_PATTERN = {
    "enable": True,
    "depth": 5,
    "ask1_bid1_max_spread": 1.0,  # максимально допустимое расстояние между бидом и аском
    "ttl": 10.0                   # sec. выдержка паттерна стакана
}

TREND_PATTERN: dict = {
    "5m": {
        "enable": True,   # включен ли индикатор
        "fast": 10,       # длина короткой волны EMA       
        "slow": 30        # длина длинной волны EMA  
    }
}


FLUSH_SIGNAL_TTL: float = 2 * 60  # 5 минут. Время жизни сигнала поистечение которого он сбрасывается из кеша сигналов. -- надо вынести глобально а не только для ценового спреда.


MAIN_FREQUENTCY: float = 0.1 # sec
SIGNAL_FREQUENCY: float = 1 # sec
SYMBOLS_FREQUENCY: float = 60 # sec

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