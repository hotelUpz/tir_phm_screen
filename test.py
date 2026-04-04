# # test_trend.py

# import asyncio
# import aiohttp
# from api import PhemexPublicApi
# from d_signal import TrendConfirmSignal

# async def test_single_symbol(symbol: str):
#     print(f"🚀 Запуск локального теста тренда для: {symbol}")
    
#     api = PhemexPublicApi()

#     # Имитируем настройки из consts.py
#     trend_config = {
#         "5m": {"enable": True, "fast": 14, "slow": 30} # Поставил 14 и 30, как на твоем скрине
#     }
#     trend_checker = TrendConfirmSignal(trend_config)
    
#     async with aiohttp.ClientSession() as session:
#         print("1. Подгружаем спецификацию биржи...")
#         await api.update_filtered_symbols(session)
        
#         if symbol not in api.filtered_symbols:
#             print(f"❌ Символ {symbol} не найден в списке активных бессрочных фьючерсов USDT.")
#             return

#         print(f"2. Запрашиваем свечи (таймфрейм {trend_checker.tf}, лимит {trend_checker.slow * 2})...")
#         klines = await api.get_klines_basic(
#             session=session,
#             symbol=symbol,
#             interval=trend_checker.tf,
#             limit=int(trend_checker.slow * 2)
#         )
        
#         if klines.empty:
#             print("❌ Свечи не получены!")
#             return

#         print(f"\n✅ Получено свечей: {len(klines)}. Хронология (первые и последние):")
#         print(klines.head(2))
#         print("...")
#         print(klines.tail(2))
        
#         print("\n3. Считаем тренд...")
#         result = trend_checker.detect_trend(klines, symbol=symbol)
        
#         print(f"\n🎯 Вердикт детектора: {result}")

# if __name__ == "__main__":
#     # Подставь сюда любую монету для проверки
#     asyncio.run(test_single_symbol("SIRENUSDT"))