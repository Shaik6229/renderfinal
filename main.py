# main.py — GOAT Crypto Alert Bot with multi-timeframe trend, ATR SL, volume spike, divergence

import os
import logging
from datetime import datetime
import pytz
import requests
import pandas as pd
from flask import Flask, request
import asyncio
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.trend import EMAIndicator

# Flask app to keep alive
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

# === New test alert endpoint ===
@app.route('/test-alert')
def test_alert():
    # Change this secret key as you want
    secret_key = "asdf"
    key = request.args.get('key')
    if key != secret_key:
        return "Unauthorized", 401

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    message = "✅ Test alert from your Crypto Alert Bot!"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        resp = requests.post(url, data=data)
        if resp.status_code == 200:
            return "Test alert sent!"
        else:
            return f"Failed to send test alert: {resp.text}", 500
    except Exception as e:
        return f"Error sending test alert: {e}", 500
# === End test alert endpoint ===

def run():
    app.run(host='0.0.0.0', port=8080)

# Globals
highs_tracker = {}  # track highest price per symbol+interval for TSL

# Fetch OHLCV from Binance public API
def fetch_ohlcv(symbol, interval, limit=500):
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}'
    try:
        data = requests.get(url).json()
        df = pd.DataFrame(data, columns=['open_time', 'open', 'high', 'low', 'close', 'volume',
                                         'close_time', 'quote_asset_volume', 'trades',
                                         'taker_buy_base', 'taker_buy_quote', 'ignore'])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        df[numeric_cols] = df[numeric_cols].astype(float)
        return df
    except Exception as e:
        logging.error(f"Error fetching OHLCV: {e}")
        return pd.DataFrame()

def is_suppressed(df):
    if df.empty or len(df) < 20:
        return True
    bb = BollingerBands(df['close'])
    width = bb.bollinger_hband() - bb.bollinger_lband()
    avg_width = width.rolling(window=20).mean().iloc[-1]
    return avg_width < 0.01 * df['close'].iloc[-1]

def fetch_ema(df, length=200):
    return EMAIndicator(df['close'], length).ema_indicator().iloc[-1]

def check_trend(symbol, interval):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 200:
        return False
    ema_200 = fetch_ema(df, 200)
    return df['close'].iloc[-1] > ema_200

def volume_spike(df):
    vol = df['volume'].iloc[-20:]
    mean_vol = vol.mean()
    std_vol = vol.std()
    current_vol = df['volume'].iloc[-1]
    return current_vol > mean_vol + 1.5 * std_vol

def rsi_divergence(df):
    rsi_vals = RSIIndicator(df['close']).rsi().iloc[-15:]
    lows_price = df['low'].iloc[-15:]
    if len(rsi_vals) < 14 or len(lows_price) < 14:
        return False
    price_lows_idx = lows_price.nsmallest(2).index.tolist()
    if len(price_lows_idx) < 2:
        return False
    first, second = price_lows_idx[0], price_lows_idx[1]
    price_condition = lows_price.loc[first] > lows_price.loc[second]
    rsi_condition = rsi_vals.loc[first] < rsi_vals.loc[second]
    return price_condition and rsi_condition

def analyze(symbol, interval, tsl_percent):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 20:
        return None

    close = df['close']
    high = df['high']
    low = df['low']

    rsi_obj = RSIIndicator(close=close)
    rsi = rsi_obj.rsi().iloc[-1]

    stoch = StochasticOscillator(high, low, close)
    k = stoch.stoch().iloc[-1]
    d = stoch.stoch_signal().iloc[-1]

    bb = BollingerBands(close)
    lower, upper = bb.bollinger_lband().iloc[-1], bb.bollinger_hband().iloc[-1]

    last = close.iloc[-1]

    vol_spike = volume_spike(df)

    # Multi-timeframe trend confirmation
    if interval == "15m":
        trend = check_trend(symbol, "1h")
    elif interval == "1h":
        trend = check_trend(symbol, "4h")
    elif interval == "1d":
        trend = check_trend(symbol, "1w")
    else:
        trend = True

    suppressed = is_suppressed(df)

    atr = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range().iloc[-1]

    recent_low = low.iloc[-5:].min()
    initial_sl = min(recent_low - atr*0.5, lower - atr*0.5)

    div = rsi_divergence(df)

    if interval == "15m":
        entry = (
            rsi < 30 and
            last <= lower and
            k < 15 and d < 15 and
            k > d and
            vol_spike and
            trend and
            not suppressed and
            div
        )
    else:
        entry = (
            rsi < 30 and
            last <= lower and
            k < 20 and d < 20 and
            k > d and
            vol_spike and
            trend and
            not suppressed
        )

    tp = last >= upper and k > 80 and d > 80

    key = f"{symbol}_{interval}"
    prev_high = highs_tracker.get(key, last)
    new_high = max(prev_high, last)
    tsl_trigger = new_high * (1 - tsl_percent)
    tsl_hit = last < tsl_trigger

    if entry:
        highs_tracker[key] = last
    elif not tsl_hit:
        highs_tracker[key] = new_high

    return {
        'symbol': symbol,
        'interval': interval,
        'price': round(last, 6),
        'rsi': round(rsi, 2),
        'stoch_k': round(k, 2),
        'stoch_d': round(d, 2),
        'entry': entry,
        'tp': tp,
        'tsl_hit': tsl_hit,
        'trend': trend,
        'suppressed': suppressed,
        'volume_spike': vol_spike,
        'bb_upper': round(upper, 6),
        'bb_lower': round(lower, 6),
        'tsl_level': round(tsl_trigger, 6),
        'highest': round(new_high, 6),
        'initial_sl': round(initial_sl, 6),
        'divergence': div
    }

def get_time():
    tz = pytz.timezone("Asia/Kolkata")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

def entry_msg(data):
    return f"""
🟢 [ENTRY] — {data['symbol']} ({data['interval']})
RSI: {data['rsi']} | Stoch %K: {data['stoch_k']} / %D: {data['stoch_d']}
Price at Lower BB ✅ | Volume Spike {'✅' if data['volume_spike'] else '❌'} | Trend: {'Bullish ✅' if data['trend'] else '❌'}
Suppression: {'Yes ❌' if data['suppressed'] else 'No ✅'}
RSI Divergence: {'Yes ✅' if data['divergence'] else 'No ❌'}
Initial SL: {data['initial_sl']}
TP Target: {data['bb_upper']} | TSL Level: {data['tsl_level']} ({round((1 - data['tsl_level']/data['highest']) * 100, 2)}%)
Price: {data['price']} | Time: {get_time()}
"""

def tp_msg(data):
    return f"""
🟡 [TAKE PROFIT] — {data['symbol']} ({data['interval']})
Price near Upper BB ✅ | RSI: {data['rsi']} | Stoch %K: {data['stoch_k']} / %D: {data['stoch_d']}
Price: {data['price']} | Time: {get_time()}
"""

def tsl_msg(data):
    return f"""
🔴 [TRAILING STOP HIT] — {data['symbol']} ({data['interval']})
Price: {data['price']} fell below TSL level: {data['tsl_level']}
Time: {get_time()}
"""

async def send_telegram_message(bot_token, chat_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        resp = requests.post(url, data=data)
        return resp.json()
    except Exception as e:
        logging.error(f"Telegram send error: {e}")
        return None

async def main_loop():
    # Your tokens and chat ID here
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    symbols = ["SUIUSDT", "SOLUSDT", "XRPUSDT"]  # Add your list here
    intervals = {
        "15m": 0.21,  # TSL percents
        "1h": 0.25,
        "1d": 0.35
    }

    while True:
        for symbol in symbols:
            for interval, tsl_percent in intervals.items():
                data = analyze(symbol, interval, tsl_percent)
                if data:
                    if data['entry']:
                        msg = entry_msg(data)
                        await send_telegram_message(bot_token, chat_id, msg)
                    elif data['tp']:
                        msg = tp_msg(data)
                        await send_telegram_message(bot_token, chat_id, msg)
                    elif data['tsl_hit']:
                        msg = tsl_msg(data)
                        await send_telegram_message(bot_token, chat_id, msg)
        await asyncio.sleep(600)  # 10 minutes

if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    from threading import Thread
    Thread(target=run).start()
    asyncio.run(main_loop())
