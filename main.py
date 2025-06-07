# main.py — Crypto Alert Bot (India Optimized Version)
# ✅ Features: Entry Alerts, Fixed TP, TSL, Suppression Detection + Manual Test

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

# Config from Environment
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SYMBOLS = [
    "SUIUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "TRXUSDT", "DOTUSDT", "RNDRUSDT", "FETUSDT", "INJUSDT",
    "AGIXUSDT", "GRTUSDT", "ILVUSDT", "SANDUSDT", "MANAUSDT",
    "ARUSDT", "PYTHUSDT", "WIFUSDT", "NEARUSDT", "PEPEUSDT"
]
INTERVALS = {"15m": 0.21, "1h": 0.25, "1d": 0.35}  # timeframe: TSL %

logging.basicConfig(filename='log.txt', level=logging.INFO, format='%(asctime)s %(message)s')

bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)
highs_tracker = {}

def get_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%Y-%m-%d %I:%M:%S %p IST")

def fetch_ohlcv(symbol, interval, limit=100):
    url = f"https://api.binance.me/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    tries = 3
    for i in range(tries):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as e:
            logging.warning(f"Attempt {i+1} failed for {symbol} {interval}: {e}")
            time.sleep(2)
    else:
        logging.error(f"All attempts failed for {symbol} {interval}")
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
    if interval == "15m":
    # Stricter entry conditions for 15m to reduce noise
    entry = (
        rsi < 30 and
        last <= lower and
        k < 15 and d < 15 and
        k > d and
        vol_spike and
        trend and
        not suppressed
    )
else:
    # For 1h and 1d keep original entry logic
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
    logging.info("Running strategy check...")
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

# Web Server + Debug/Test Routes

@app.route('/')
def home():
    return "✅ GOAT Crypto Bot is Running!"

@app.route('/trigger')
def trigger():
    run_strategy()
    return "✅ Strategy run completed."

@app.route('/tg-test')
def test_alert():
    try:
        logging.info(f"Sending test alert to chat_id={CHAT_ID} with token prefix={BOT_TOKEN[:10]}...")
        resp = bot.send_message(CHAT_ID, text=f"✅ [TEST ALERT] — Your Crypto Bot is working!\nTime: {get_time()}")
        logging.info(f"Telegram response: {resp}")
        return "✅ Test alert sent!"
    except Exception as e:
        logging.error(f"Test alert error: {e}", exc_info=True)
        return f"❌ Error sending test alert: {e}"

@app.route('/env-check')
def env_check():
    token = os.getenv("BOT_TOKEN")
    chat = os.getenv("CHAT_ID")
    token_display = token[:5] + "..." if token else "None"
    return f"BOT_TOKEN starts with: {token_display}, CHAT_ID: {chat}"

@app.route('/telegram-ping')
def telegram_ping():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    try:
        r = requests.get(url, timeout=10)
        return f"Telegram getMe response: {r.json()}"
    except Exception as e:
        return f"Error contacting Telegram API: {e}"

@app.route('/send-test-msg')
def send_test_msg():
    try:
        resp = bot.send_message(CHAT_ID, text="Hello from minimal test script!")
        return f"Sent message, Telegram response: {resp}"
    except Exception as e:
        return f"Error sending test message: {e}"

if __name__ == '__main__':
    scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Kolkata'))
    scheduler.add_job(run_strategy, 'interval', minutes=10)
    scheduler.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
