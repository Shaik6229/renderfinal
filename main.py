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

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SYMBOLS = [
    "SUIUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "TRXUSDT", "DOTUSDT", "RNDRUSDT", "FETUSDT", "INJUSDT",
    "AGIXUSDT", "GRTUSDT", "ILVUSDT", "SANDUSDT", "MANAUSDT"
]
INTERVALS = {
    "15m": "15m",
    "1h": "1h",
    "1d": "1d"
}

# Setup logging
logging.basicConfig(filename='log.txt', level=logging.INFO, format='%(asctime)s %(message)s')
logging.info("Bot initialized and ready.")

# Telegram bot
bot = Bot(token=BOT_TOKEN)

# Flask app
app = Flask(__name__)

# Time formatter
def get_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%Y-%m-%d %I:%M:%S %p IST")

# Fetch OHLCV from Binance
def fetch_ohlcv(symbol, interval, limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    resp = requests.get(url)
    data = resp.json()
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        '_', '_', '_', '_', '_'
    ])
    df['close'] = df['close'].astype(float)
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# Analyze indicators
def analyze(symbol, interval):
    df = fetch_ohlcv(symbol, interval)
    if df.empty:
        return None

    close = df['close']

    rsi = RSIIndicator(close=close).rsi().iloc[-1]
    stoch_k = StochasticOscillator(high=df['high'], low=df['low'], close=close).stoch().iloc[-1]
    stoch_d = StochasticOscillator(high=df['high'], low=df['low'], close=close).stoch_signal().iloc[-1]
    bb = BollingerBands(close=close)
    bb_lower = bb.bollinger_lband().iloc[-1]
    bb_upper = bb.bollinger_hband().iloc[-1]
    last_price = close.iloc[-1]

    # Determine entry signal
    entry = rsi < 30 and last_price <= bb_lower and stoch_k < 20 and stoch_d < 20
    tp = last_price >= bb_upper and stoch_k > 80 and stoch_d > 80

    # Support/Resistance: Recent swing lows/highs
    support = df['low'].rolling(window=20).min().iloc[-1]
    resistance = df['high'].rolling(window=20).max().iloc[-1]

    return {
        'symbol': symbol,
        'interval': interval,
        'price': round(last_price, 4),
        'rsi': round(rsi, 2),
        'stoch_k': round(stoch_k, 2),
        'stoch_d': round(stoch_d, 2),
        'entry': entry,
        'tp': tp,
        'support': round(support, 4),
        'resistance': round(resistance, 4),
        'bb_upper': round(bb_upper, 4),
        'bb_lower': round(bb_lower, 4)
    }

# Alert formatting

def generate_entry_msg(data):
    return f"""
🟢 [ENTRY ALERT] — {data['symbol']} ({data['interval']})
RSI: {data['rsi']}
Stochastic %K: {data['stoch_k']} | %D: {data['stoch_d']}
Price touching lower Bollinger Band: ✅

Current Price: {data['price']} USDT
Support: {data['support']} | Resistance: {data['resistance']}

Trend: Bullish ✅
Volume Spike: Possibly

📋 Timeframe: {data['interval']}
⏰ Time: {get_time()}"""

def generate_tp_msg(data):
    return f"""
🔹 [TAKE PROFIT SIGNAL] — {data['symbol']} ({data['interval']})
Stochastic %K: {data['stoch_k']} | %D: {data['stoch_d']}
Price near upper Bollinger Band: ✅

Current Price: {data['price']} USDT
Support: {data['support']} | Resistance: {data['resistance']}

📋 Timeframe: {data['interval']}
⏰ Time: {get_time()}"""

# Strategy

def run_strategy():
    for symbol in SYMBOLS:
        for interval in INTERVALS:
            try:
                result = analyze(symbol, INTERVALS[interval])
                if result is None:
                    continue

                if result['entry']:
                    msg = generate_entry_msg(result)
                    bot.send_message(chat_id=CHAT_ID, text=msg)
                    logging.info(f"ENTRY alert sent for {symbol} {interval}")

                if interval in ["1h", "1d"] and result['tp']:
                    msg = generate_tp_msg(result)
                    bot.send_message(chat_id=CHAT_ID, text=msg)
                    logging.info(f"TP alert sent for {symbol} {interval}")
            except Exception as e:
                logging.error(f"Error processing {symbol} {interval}: {str(e)}")

# Initial test
try:
    test_data = analyze("SOLUSDT", "15m")
    if test_data:
        bot.send_message(chat_id=CHAT_ID, text="[TEST ALERT]\n" + generate_entry_msg(test_data))
        logging.info("Test alert sent.")
except Exception as e:
    logging.error(f"Test alert error: {e}")

# Scheduler
scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Kolkata'))
scheduler.add_job(run_strategy, 'interval', minutes=10)
scheduler.start()

# Flask endpoint
@app.route('/')
def home():
    return "Bot is running"

# Run Flask
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
