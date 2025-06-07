# main.py — GOAT Crypto Alert Bot
# Features: Entry Alerts, Take Profit, Trailing SL, Suppression Detection

import os
import logging
from datetime import datetime
import pytz
import requests
import pandas as pd
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands
from ta.trend import EMAIndicator

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SYMBOLS = [
    "SUIUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "TRXUSDT", "DOTUSDT", "RNDRUSDT", "FETUSDT", "INJUSDT",
    "AGIXUSDT", "GRTUSDT", "ILVUSDT", "SANDUSDT", "MANAUSDT",
    "ARUSDT", "PYTHUSDT", "WIFUSDT", "NEARUSDT", "PEPEUSDT"
]
INTERVALS = {"15m": 0.21, "1h": 0.25, "1d": 0.35}  # Timeframe : TSL %

logging.basicConfig(filename='log.txt', level=logging.INFO, format='%(asctime)s %(message)s')

bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)
highs_tracker = {}

def get_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%Y-%m-%d %I:%M:%S %p IST")

def fetch_ohlcv(symbol, interval, limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logging.error(f"Error fetching {symbol} {interval}: {e}")
        return pd.DataFrame()
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore']
    )
    df['close'] = df['close'].astype(float)
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    df['volume'] = df['volume'].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def is_suppressed(df):
    bb = BollingerBands(close=df['close'])
    width = bb.bollinger_hband() - bb.bollinger_lband()
    price_range = df['high'] - df['low']
    return width.iloc[-1] < price_range.rolling(20).mean().iloc[-1] * 0.5

def analyze(symbol, interval, tsl_percent):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 20:
        return None

    close = df['close']
    rsi = RSIIndicator(close=close).rsi().iloc[-1]
    stoch = StochasticOscillator(df['high'], df['low'], close)
    k = stoch.stoch().iloc[-1]
    d = stoch.stoch_signal().iloc[-1]
    bb = BollingerBands(close)
    lower, upper = bb.bollinger_lband().iloc[-1], bb.bollinger_hband().iloc[-1]
    last = close.iloc[-1]

    vol_spike = df['volume'].iloc[-1] > df['volume'].rolling(20).mean().iloc[-1]
    trend = True
    if interval == "15m":
        df_1h = fetch_ohlcv(symbol, "1h")
        if not df_1h.empty and len(df_1h) >= 200:
            ema_200 = EMAIndicator(df_1h['close'], 200).ema_indicator().iloc[-1]
            trend = df_1h['close'].iloc[-1] > ema_200
        else:
            trend = False

    suppressed = is_suppressed(df)
    entry = rsi < 30 and last <= lower and k < 20 and d < 20 and k > d and vol_spike and trend and not suppressed
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
        'symbol': symbol, 'interval': interval, 'price': round(last, 4),
        'rsi': round(rsi, 2), 'stoch_k': round(k, 2), 'stoch_d': round(d, 2),
        'entry': entry, 'tp': tp, 'tsl_hit': tsl_hit, 'trend': trend,
        'suppressed': suppressed, 'volume_spike': vol_spike,
        'bb_upper': round(upper, 4), 'bb_lower': round(lower, 4),
        'tsl_level': round(tsl_trigger, 4), 'highest': round(new_high, 4)
    }

def entry_msg(data):
    return f"""
🟢 [ENTRY] — {data['symbol']} ({data['interval']})
RSI: {data['rsi']} | Stoch %K: {data['stoch_k']} / %D: {data['stoch_d']}
Price at Lower BB ✅ | Volume Spike ✅ | Trend: {'Bullish ✅' if data['trend'] else '❌'}
Suppression: {'Yes ❌' if data['suppressed'] else 'No ✅'}
TP Target: {data['bb_upper']} | TSL: {round((1 - data['tsl_level']/data['highest']) * 100, 2)}%
Price: {data['price']} | Time: {get_time()}
"""

def tp_msg(data):
    return f"""
🔹 [TAKE PROFIT] — {data['symbol']} ({data['interval']})
Stoch %K: {data['stoch_k']} | %D: {data['stoch_d']}
Upper BB Touched ✅
Price: {data['price']} | TP: {data['bb_upper']} | Time: {get_time()}
"""

def tsl_msg(data):
    return f"""
🔻 [TSL HIT] — {data['symbol']} ({data['interval']})
Trailing SL Hit Below {data['tsl_level']} (High: {data['highest']})
Exit Suggested ✅
Price: {data['price']} | Time: {get_time()}
"""

def run_strategy():
    for symbol in SYMBOLS:
        for interval, tsl_pct in INTERVALS.items():
            try:
                result = analyze(symbol, interval, tsl_pct)
                if not result:
                    continue
                if result['entry']:
                    bot.send_message(CHAT_ID, text=entry_msg(result))
                elif result['tp']:
                    bot.send_message(CHAT_ID, text=tp_msg(result))
                elif result['tsl_hit']:
                    bot.send_message(CHAT_ID, text=tsl_msg(result))
            except Exception as e:
                logging.error(f"Error with {symbol} {interval}: {e}")

@app.route('/trigger')
def trigger():
    run_strategy()
    return "Strategy run completed!"

@app.route('/test')
def test_alert():
    bot.send_message(CHAT_ID, text="✅ Test Alert: Your crypto bot is running and able to send Telegram messages.")
    return "Test alert sent to Telegram!"

@app.route('/')
def home():
    return "GOAT Crypto Bot is Alive!"

if __name__ == '__main__':
    scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Kolkata'))
    scheduler.add_job(run_strategy, 'interval', minutes=10)
    scheduler.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
