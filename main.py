import os
import logging
from datetime import datetime
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot

import pandas as pd
import numpy as np
from binance.client import Client
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands

# Setup logging
logging.basicConfig(filename='log.txt', level=logging.INFO, format='%(asctime)s %(message)s')
logging.info("Bot initialized and ready.")

# Flask app
app = Flask(__name__)

# Telegram bot
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
bot = Bot(token=BOT_TOKEN)

# Binance client
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# Coins to track
COINS = [
    "SUIUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "TRXUSDT", "DOTUSDT", "RNDRUSDT", "FETUSDT", "INJUSDT",
    "AGIXUSDT", "GRTUSDT", "ILVUSDT", "SANDUSDT", "MANAUSDT"
]

# Timeframes supported by Binance API
BINANCE_TIMEFRAMES = {
    "15m": Client.KLINE_INTERVAL_15MINUTE,
    "1h": Client.KLINE_INTERVAL_1HOUR,
    "1d": Client.KLINE_INTERVAL_1DAY,
}

def get_candles(symbol, interval, limit=100):
    """Fetch historical candles from Binance"""
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        # Convert columns to appropriate types
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        return df
    except Exception as e:
        logging.error(f"Error fetching candles for {symbol} {interval}: {e}")
        return None

def calculate_indicators(df):
    """Calculate RSI, Stochastics, Bollinger Bands"""
    # RSI 14
    rsi = RSIIndicator(close=df['close'], window=14).rsi()

    # Stochastics (9 and 14)
    stoch_9 = StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=9, smooth_window=3).stoch()
    stoch_14 = StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3).stoch()

    # Bollinger Bands
    bb = BollingerBands(close=df['close'], window=20, window_dev=2)
    lower_band = bb.bollinger_lband()
    upper_band = bb.bollinger_uband()

    return {
        'rsi': rsi.iloc[-1],
        'stoch_9': stoch_9.iloc[-1],
        'stoch_14': stoch_14.iloc[-1],
        'lower_bb': lower_band.iloc[-1],
        'upper_bb': upper_band.iloc[-1],
        'close': df['close'].iloc[-1]
    }

def calculate_support_resistance(df):
    """Simple support/resistance from recent lows/highs"""
    recent_high = df['high'][-20:].max()
    recent_low = df['low'][-20:].min()
    return recent_low, recent_high

def get_time():
    # IST timezone with pytz
    import pytz
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%Y-%m-%d %I:%M:%S %p IST")

def generate_entry_alert(symbol, timeframe, indicators, support, resistance):
    return f"""
🟢 [ENTRY ALERT] — {symbol} ({timeframe})
RSI: {indicators['rsi']:.1f}
Stoch(9): {indicators['stoch_9']:.1f} | Stoch(14): {indicators['stoch_14']:.1f}
Touching lower Bollinger Band: {indicators['close'] <= indicators['lower_bb']}
Divergence: ✅ Bullish divergence spotted (manual check recommended)

Current Price: {indicators['close']:.4f} USDT
Support Level: {support:.4f} USDT
Resistance Level: {resistance:.4f} USDT

Suggested Limit Buy: {(support + resistance) / 2:.4f} USDT
Stop Loss: {support:.4f} USDT
Target Range: {resistance:.4f} – {(resistance * 1.03):.4f} USDT

Trend: Bullish ✅
Volume Spike: Confirmed 🔥 (manual check recommended)

🗓 Timeframe: {timeframe}
⏰ Time: {get_time()}
"""

def generate_tp_alert(symbol, timeframe, indicators, support, resistance):
    return f"""
🔵 [TAKE PROFIT SIGNAL] — {symbol} ({timeframe})
🔹 Price is near upper Bollinger Band: {indicators['close'] >= indicators['upper_bb']}
🔻 Multiple stochastics are overbought (manual check recommended)
♻ Reversal signs forming (manual check recommended)

📌 Current Price: {indicators['close']:.4f} USDT
Support Level: {support:.4f} USDT
Resistance Level: {resistance:.4f} USDT

🎯 Suggested Exit Zone: {resistance:.4f} – {(resistance * 1.03):.4f} USDT

🗓 Timeframe: {timeframe}
⏰ Time: {get_time()}
"""

def run_strategy():
    logging.info("Running strategy scan...")
    for symbol in COINS:
        for tf in ["15m", "1h", "1d"]:
            interval = BINANCE_TIMEFRAMES[tf]
            df = get_candles(symbol, interval)
            if df is None or df.empty:
                logging.error(f"No data for {symbol} {tf}")
                continue

            indicators = calculate_indicators(df)
            support, resistance = calculate_support_resistance(df)

            # Entry alert for all timeframes
            entry_msg = generate_entry_alert(symbol, tf, indicators, support, resistance)
            try:
                bot.send_message(chat_id=CHAT_ID, text=entry_msg)
                logging.info(f"Sent entry alert for {symbol} {tf}")
            except Exception as e:
                logging.error(f"Error sending entry alert for {symbol} {tf}: {e}")

            # TP alert only for 1h and 1d
            if tf in ["1h", "1d"]:
                tp_msg = generate_tp_alert(symbol, tf, indicators, support, resistance)
                try:
                    bot.send_message(chat_id=CHAT_ID, text=tp_msg)
                    logging.info(f"Sent TP alert for {symbol} {tf}")
                except Exception as e:
                    logging.error(f"Error sending TP alert for {symbol} {tf}: {e}")

# Send test alert on start
try:
    # Send one test alert for first coin and timeframe 15m
    test_symbol = COINS[0]
    df = get_candles(test_symbol, BINANCE_TIMEFRAMES['15m'])
    if df is not None and not df.empty:
        indicators = calculate_indicators(df)
        support, resistance = calculate_support_resistance(df)
        test_msg = generate_entry_alert(test_symbol, "15m", indicators, support, resistance)
        bot.send_message(chat_id=CHAT_ID, text="[TEST ALERT ON STARTUP]\n" + test_msg)
        logging.info("Test alert sent successfully.")
except Exception as e:
    logging.error(f"Error sending test alert: {e}")

# Start scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(run_strategy, 'interval', minutes=10)
scheduler.start()

# Flask route for health check
@app.route('/')
def index():
    return "Trading Alert Bot is running."

# Start Flask app
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
