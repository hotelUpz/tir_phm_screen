import asyncio
import aiohttp
import random
import time
from typing import *
from decimal import Decimal, getcontext, ROUND_HALF_UP
from consts import HOT_FAIR_PATTERN, PRECISION, MIN_SEND_INTERVAL, TG_BOT_TOKEN, CHAT_IDS
from c_log import UnifiedLogger

logger = UnifiedLogger("tg")

class TelegramNotifier:
    def __init__(self, stop_bot: bool):
        self.token = TG_BOT_TOKEN
        self.chat_ids = [x.strip() for x in CHAT_IDS if x and isinstance(x, str)]
        self.base_tg_url = f"https://api.telegram.org/bot{self.token}"
        self.send_text_endpoint = "/sendMessage"
        self.send_photo_endpoint = "/sendPhoto"
        self.stop_bot = stop_bot
        self._lock = asyncio.Lock()
        self._last_send_time = 0.0

    async def send(
        self,
        text: str,
        photo_bytes: bytes = None,
        disable_notification: bool = False,
        max_retries: int = 2,
    ):
        async def _try_send(session: aiohttp.ClientSession, chat_id):
            if photo_bytes:
                url = self.base_tg_url + self.send_photo_endpoint
                data = aiohttp.FormData()
                data.add_field("chat_id", str(chat_id))
                data.add_field("caption", text or "")
                data.add_field("parse_mode", "HTML")
                data.add_field("disable_web_page_preview", "true")
                data.add_field("disable_notification", str(disable_notification).lower())
                data.add_field("photo", photo_bytes, filename="spread.png", content_type="image/png")
            else:
                url = self.base_tg_url + self.send_text_endpoint
                data = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "disable_notification": disable_notification,
                }

            attempt = 0
            while not self.stop_bot:
                attempt += 1
                async with self._lock:
                    elapsed = time.monotonic() - self._last_send_time
                    if elapsed < MIN_SEND_INTERVAL:
                        await asyncio.sleep(MIN_SEND_INTERVAL - elapsed)
                    
                    try:
                        async with session.post(url, data=data, timeout=10) as resp:
                            if resp.status != 200:
                                err_text = await resp.text()
                                raise Exception(f"HTTP {resp.status}: {err_text}")
                            self._last_send_time = time.monotonic()
                            return True
                    except Exception as e:
                        wait_time = random.uniform(1, 3)
                        logger.error(f"[TG] Попытка {attempt}/{max_retries} не удалась ({e}), повтор через {wait_time:.1f}с")
                        if attempt == max_retries:
                            return False
                        await asyncio.sleep(wait_time)

        async with aiohttp.ClientSession() as session:
            tasks = [_try_send(session, chat_id) for chat_id in self.chat_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return all(r is True for r in results)     

class Formatter:
    @staticmethod
    def to_human_digit(value):
        if value is None:
            return "N/A"
        getcontext().prec = PRECISION
        # Нормализуем и отсекаем нули чисто математически
        dec_value = Decimal(str(value)).normalize()
        str_val = format(dec_value, 'f')
        if '.' in str_val:
            str_val = str_val.rstrip('0').rstrip('.')
        return str_val

    @staticmethod
    def format_coins_for_tg(
        signals_data: List[Dict],
        title: str = None
    ) -> str:

        if not signals_data:
            return ""

        lines = []

        for s in signals_data:
            max_lvg = s.get("max_lvg", 20)
            diff_pct_lvg_depend = HOT_FAIR_PATTERN.get("lever_dependencies", {})
            lev_key = next((item for item in diff_pct_lvg_depend.keys() if item[0] <= max_lvg <= item[1]), (20, 40))
            diff_cfg = diff_pct_lvg_depend.get(lev_key).get("spread", 5.0)    

            if not title: title = f"Fair > Last (Δ ≥ {diff_cfg}%)"

            # 🛑 ФИКС ОКРУГЛЕНИЯ: Переводим всё в Decimal для точной математики без багов Float
            prec_str = str(s.get("price_precision") or 0.0001)
            prec_dec = Decimal(prec_str)
            
            last_dec = Decimal(str(s["last_price"]))
            fair_dec = Decimal(str(s["fair_price"]))
            diff = s["diff_percent"]
            trend_msg = s.get("trend_msg", "-")
            stakan_msg = s.get("stakan_msg", "-")

            # Железобетонное округление кратно шагу цены биржи
            if prec_dec > 0:
                rounded_last = (last_dec / prec_dec).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * prec_dec
                rounded_fair = (fair_dec / prec_dec).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * prec_dec
            else:
                rounded_last = last_dec
                rounded_fair = fair_dec
            
            str_last = Formatter.to_human_digit(rounded_last)
            str_fair = Formatter.to_human_digit(rounded_fair)

            if diff >= diff_cfg:
                icon = "🟢"
            elif diff <= -diff_cfg:
                icon = "🔴"
            else:
                icon = "⚪"

            lines.append(
                f"<b>[ {title} ]</b>\n"
                f"{icon} <b>#{s['symbol']}</b>\n"
                f"L: <code>{str_last:<10}</code> F: <code>{str_fair:<10}</code>\n"
                f"Δ: {diff:+.2f}%\n"
                f"Stakan: {stakan_msg}\n"
                f"Trend: {trend_msg}\n"
                f"Max Lev: {max_lvg}\n"
            )

        return "\n".join(lines)