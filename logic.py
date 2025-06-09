import asyncio
import os
from utils import fetch_ohlcv, check_trend, is_suppressed, volume_spike, rsi_divergence, get_time
from alerts import entry_msg, tp_msg, tsl_msg, send_telegram_message
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands, AverageTrueRange

highs_tracker = {}
alert_flags = {}  # Tracks last alert state per symbol+interval+type

def start_bot():
    asyncio.run(main_loop())

async def main_loop():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "INJUSDT", "LINAUSDT"]
    intervals = {"15m": 0.21, "1h": 0.25, "1d": 0.35}

    while True:
        for symbol in SYMBOLS:
            for interval, tsl_percent in intervals.items():
                try:
                    df = fetch_ohlcv(symbol, interval)
                    if df.empty or len(df) < 20:
                        continue

                    close = df['close']
                    high = df['high']
                    low = df['low']
                    rsi = RSIIndicator(close).rsi().iloc[-1]
                    stoch = StochasticOscillator(high, low, close)
                    k = stoch.stoch().iloc[-1]
                    d = stoch.stoch_signal().iloc[-1]
                    bb = BollingerBands(close)
                    lower = bb.bollinger_lband().iloc[-1]
                    upper = bb.bollinger_hband().iloc[-1]
                    last = close.iloc[-1]
                    vol = volume_spike(df)
                    trend = check_trend(symbol, "1h" if interval == "15m" else "4h" if interval == "1h" else "1w")
                    suppressed = is_suppressed(df)
                    div = rsi_divergence(df)
                    atr = AverageTrueRange(high, low, close).average_true_range().iloc[-1]
                    recent_low = low.iloc[-5:].min()
                    initial_sl = min(recent_low - atr * 0.5, lower - atr * 0.5)
                    key = f"{symbol}_{interval}"
                    prev_high = highs_tracker.get(key, last)
                    new_high = max(prev_high, last)
                    tsl_trigger = new_high * (1 - tsl_percent)
                    tsl_hit = last < tsl_trigger
                    if vol and trend and not suppressed:
                        highs_tracker[key] = last if last > prev_high else prev_high
                    elif not tsl_hit:
                        highs_tracker[key] = new_high

                    confidence = 0
                    confidence += 20 if vol else 0
                    confidence += 20 if trend else 0
                    confidence += 15 if not suppressed else 0
                    confidence += 15 if div else 0
                    confidence += 15 if rsi < 30 else 0
                    confidence += 15 if k < 20 and d < 20 else 0
                    confidence = min(confidence, 100)

                    data = {
                        'symbol': symbol, 'interval': interval, 'price': round(last, 6),
                        'rsi': round(rsi, 2), 'stoch_k': round(k, 2), 'stoch_d': round(d, 2),
                        'entry': confidence >= 70, 'tp': last >= upper and k > 80 and d > 80,
                        'tsl_hit': tsl_hit, 'trend': trend, 'suppressed': suppressed,
                        'volume_spike': vol, 'bb_upper': round(upper, 6), 'bb_lower': round(lower, 6),
                        'tsl_level': round(tsl_trigger, 6), 'highest': round(new_high, 6),
                        'initial_sl': round(initial_sl, 6), 'divergence': div, 'confidence': confidence
                    }

                    alert_key = f"{symbol}_{interval}"
                    
                    # ENTRY
                    if data['entry']:
                        if alert_flags.get(alert_key + "_entry") != True:
                            await send_telegram_message(bot_token, chat_id, entry_msg(data))
                            alert_flags[alert_key + "_entry"] = True
                    else:
                        alert_flags[alert_key + "_entry"] = False
                    
                    # TP
                    if data['tp']:
                        if alert_flags.get(alert_key + "_tp") != True:
                            await send_telegram_message(bot_token, chat_id, tp_msg(data))
                            alert_flags[alert_key + "_tp"] = True
                    else:
                        alert_flags[alert_key + "_tp"] = False
                    
                    # TSL
                    if data['tsl_hit']:
                        if alert_flags.get(alert_key + "_tsl") != True:
                            await send_telegram_message(bot_token, chat_id, tsl_msg(data))
                            alert_flags[alert_key + "_tsl"] = True
                    else:
                        alert_flags[alert_key + "_tsl"] = False

                except Exception as e:
                    print(f"Error in main loop for {symbol} {interval}: {e}")
        await asyncio.sleep(600)
