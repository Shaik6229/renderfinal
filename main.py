import os
import logging
from datetime import datetime
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
import requests
import pandas as pd
from ta.momentum import RSIIndicator, StochasticOscillator
import pytz

# Setup logging
logging.basicConfig(filename='log.txt', level=logging.INFO, format='%(asctime)s %(message)s')
logging.info("Bot initialized and ready.")

# Flask app
app = Flask(__name__)

# Telegram bot
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID not set in environment variables")
    raise ValueError("BOT_TOKEN and CHAT_ID must be set")

bot = Bot(token=BOT_TOKEN)

BINANCE_API = "https://api.binance.com/api/v3/klines"

def fetch_klines(symbol, interval, limit=100):
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    response = requests.get(BINANCE_API, params=params)
    data = response.json()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit='ms')
    df["close_time"] = pd.to_datetime(df["close_time"], unit='ms')
    return df

def calculate_support_resistance(df):
    # Use previous candle for pivot points
    high = df['high'].iloc[-2]
    low = df['low'].iloc[-2]
    close = df['close'].iloc[-2]
    pivot = (high + low + close) / 3
    support = 2 * pivot - high
    resistance = 2 * pivot - low
    return round(support, 4), round(resistance, 4)

def get_real_indicators(symbol, interval):
    df = fetch_klines(symbol, interval, limit=100)
    close = df['close']
    rsi_indicator = RSIIndicator(close, window=14)
    rsi = rsi_indicator.rsi().iloc[-1]
    stoch = StochasticOscillator(df['high'], df['low'], close, window=14, smooth_window=3)
    stoch_k = stoch.stoch().iloc[-1]
    stoch_d = stoch.stoch_signal().iloc[-1]
    support, resistance = calculate_support_resistance(df)
    current_price = close.iloc[-1]
    stop_loss = round(current_price * 0.99, 4)  # example: 1% below current price
    target_low = round(current_price * 1.02, 4)  # example target range
    target_high = round(current_price * 1.03, 4)
    return {
        "rsi": round(rsi, 2),
        "stoch_k": round(stoch_k, 2),
        "stoch_d": round(stoch_d, 2),
        "support": support,
        "resistance": resistance,
        "current_price": round(current_price, 4),
        "stop_loss": stop_loss,
        "target_low": target_low,
        "target_high": target_high
    }

def get_time():
    # Return IST time formatted
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%Y-%m-%d %I:%M:%S %p IST")

def generate_entry_alert(symbol="SOLUSDT", timeframe="15m"):
    ind = get_real_indicators(symbol, timeframe)
    return f"""
🟢 [ENTRY ALERT] — {symbol} ({timeframe})
RSI: {ind['rsi']}
Stoch %K: {ind['stoch_k']} | Stoch %D: {ind['stoch_d']}
Support: {ind['support']}
Resistance: {ind['resistance']}

Current Price: {ind['current_price']} USDT
Stop Loss: {ind['stop_loss']} USDT
Target Range: {ind['target_low']} – {ind['target_high']} USDT

Trend: Bullish ✅
Volume Spike: Confirmed 🔥

🗓 Timeframe: {timeframe}
⏰ Time: {get_time()}"""

def generate_tp_alert(symbol="SOLUSDT", timeframe="1h"):
    ind = get_real_indicators(symbol, timeframe)
    return f"""
🔵 [TAKE PROFIT SIGNAL] — {symbol} ({timeframe})
Price near resistance zone
Stochastics showing overbought conditions

Current Price: {ind['current_price']} USDT
Target Range: {ind['target_low']} – {ind['target_high']} USDT

🗓 Timeframe: {timeframe}
⏰ Time: {get_time()}"""

# List of coins to scan
COINS = [
    "SUIUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "TRXUSDT", "DOTUSDT", "RNDRUSDT", "FETUSDT", "INJUSDT",
    "AGIXUSDT", "GRTUSDT", "ILVUSDT", "SANDUSDT", "MANAUSDT"
]

def run_strategy():
    logging.info("Running strategy scan...")
    timeframes = ["15m", "1h", "1d"]
    for symbol in COINS:
        for tf in timeframes:
            try:
                entry_msg = generate_entry_alert(symbol, tf)
                bot.send_message(chat_id=CHAT_ID, text=entry_msg)
                logging.info(f"Sent entry alert for {symbol} {tf}")
                # Only TP alert for 1h and 1d
                if tf in ["1h", "1d"]:
                    tp_msg = generate_tp_alert(symbol, tf)
                    bot.send_message(chat_id=CHAT_ID, text=tp_msg)
                    logging.info(f"Sent TP alert for {symbol} {tf}")
            except Exception as e:
                logging.error(f"Error sending alert for {symbol} {tf}: {e}")

# Send test alert on start
try:
    test_msg = generate_entry_alert()
    bot.send_message(chat_id=CHAT_ID, text="[TEST ALERT ON STARTUP]\n" + test_msg)
    logging.info("Test alert sent successfully.")
except Exception as e:
    logging.error(f"Error sending test alert: {e}")

# Scheduler setup with pytz timezone to avoid timezone errors
scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Kolkata'))
scheduler.add_job(run_strategy, 'interval', minutes=10)
scheduler.start()

@app.route('/')
def index():
    return "Trading Alert Bot is running."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
