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
if not BOT_TOKEN or not CHAT_ID:
    logging.error("BOT_TOKEN or CHAT_ID environment variable missing.")
    raise ValueError("BOT_TOKEN or CHAT_ID environment variable missing.")

bot = Bot(token=BOT_TOKEN)

# Flask app
app = Flask(__name__)

# Time formatter
def get_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%Y-%m-%d %I:%M:%S %p IST")

# Fetch OHLCV from Binance with error handling
def fetch_ohlcv(symbol, interval, limit=100):
    url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"Fetched {len(data)} candles for {symbol} {interval}")
    except Exception as e:
        logging.error(f"Error fetching OHLCV for {symbol} {interval}: {e}")
        print(f"Error fetching OHLCV for {symbol} {interval}: {e}")
        return pd.DataFrame()

    if not data or not isinstance(data, list):
        logging.error(f"Invalid data received for {symbol} {interval}")
        print(f"Invalid data received for {symbol} {interval}")
        return pd.DataFrame()

    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    df['close'] = df['close'].astype(float)
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    df['volume'] = df['volume'].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# Analyze indicators
def analyze(symbol, interval):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 20:
        logging.warning(f"Insufficient data for {symbol} {interval}")
        return None

    close = df['close']

    rsi = RSIIndicator(close=close).rsi().iloc[-1]
    stoch = StochasticOscillator(high=df['high'], low=df['low'], close=close)
    stoch_k = stoch.stoch().iloc[-1]
    stoch_d = stoch.stoch_signal().iloc[-1]
    bb = BollingerBands(close=close)
    bb_lower = bb.bollinger_lband().iloc[-1]
    bb_upper = bb.bollinger_hband().iloc[-1]
    last_price = close.iloc[-1]
    last_volume = df['volume'].iloc[-1]
    avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
    volume_spike = last_volume > avg_volume

    trend_bullish = True
    if interval == "15m":
        df_1h = fetch_ohlcv(symbol, "1h")
        if not df_1h.empty and len(df_1h) >= 200:
            ema_200 = EMAIndicator(close=df_1h['close'], window=200).ema_indicator().iloc[-1]
            current_price_1h = df_1h['close'].iloc[-1]
            trend_bullish = current_price_1h > ema_200
        else:
            logging.warning(f"Insufficient 1h data for trend analysis of {symbol}")
            trend_bullish = False

    entry = (
        rsi < 30 and
        last_price <= bb_lower and
        stoch_k < 20 and stoch_d < 20 and stoch_k > stoch_d and
        volume_spike and
        (trend_bullish if interval == "15m" else True)
    )

    tp = (last_price >= bb_upper) and (stoch_k > 80) and (stoch_d > 80)

    support = df['low'].rolling(window=20).min().iloc[-1]
    resistance = df['high'].rolling(window=20).max().iloc[-1]

    trailing_sl = None
    if interval == "1d":
        trailing_sl = round(last_price * (1 - 0.35), 4)

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
        'bb_lower': round(bb_lower, 4),
        'trend': trend_bullish,
        'volume_spike': volume_spike,
        'trailing_sl': trailing_sl
    }

# Alert formatting

def generate_entry_msg(data):
    msg = f"""
🟢 [ENTRY ALERT] — {data['symbol']} ({data['interval']})
RSI: {data['rsi']}
Stochastic %K: {data['stoch_k']} | %D: {data['stoch_d']}
Price touching lower Bollinger Band: ✅

Current Price: {data['price']} USDT
Support: {data['support']} | Resistance: {data['resistance']}
"""
    if data['interval'] == "15m":
        msg += f"Suggested Exit (TP) Price: {data['bb_upper']} USDT\n"
    if data['interval'] == "1d":
        msg += f"Trailing Stop Loss: {data['trailing_sl']} USDT\n"

    msg += f"""

Trend: {'Bullish ✅' if data['trend'] else 'Bearish ❌'}
Volume Spike: {'Yes ✅' if data['volume_spike'] else 'No ❌'}

📋 Timeframe: {data['interval']}
⏰ Time: {get_time()}"""
    return msg

def generate_tp_msg(data):
    return f"""
🔹 [TAKE PROFIT SIGNAL] — {data['symbol']} ({data['interval']})
Stochastic %K: {data['stoch_k']} | %D: {data['stoch_d']}
Price near upper Bollinger Band: ✅

Current Price: {data['price']} USDT
Support: {data['support']} | Resistance: {data['resistance']}

📋 Timeframe: {data['interval']}
⏰ Time: {get_time()}"""

def generate_test_alert(data):
    msg = f"🔎 [TEST ALERT] {data['symbol']} ({data['interval']})\n\n"
    msg += f"Current Price: {data['price']} USDT\n\n"

    if data['entry']:
        msg += "Entry Status: ✅ Good to enter now!\n"
        msg += "Explanation: RSI is low, price is touching lower Bollinger Band, and Stochastic is oversold. These indicators suggest a potential bounce up.\n"
    else:
        msg += "Entry Status: ❌ Better to wait!\n"
        msg += "Explanation: RSI or other indicators do not currently suggest a good entry point.\n"

    msg += f"\n⏰ Time: {get_time()}"
    return msg

# Strategy
def run_strategy():
    for symbol in SYMBOLS:
        for interval in INTERVALS:
            try:
                result = analyze(symbol, INTERVALS[interval])
                if result is None:
                    logging.info(f"No data or insufficient data for {symbol} {interval}, skipping.")
                    continue

                if result['entry']:
                    msg = generate_entry_msg(result)
                    bot.send_message(chat_id=CHAT_ID, text=msg)
                    logging.info(f"ENTRY alert sent for {symbol} {interval}")
                else:
                    logging.info(f"No ENTRY condition met for {symbol} {interval}")

                if interval == "1h" and result['tp']:
                    msg = generate_tp_msg(result)
                    bot.send_message(chat_id=CHAT_ID, text=msg)
                    logging.info(f"TP alert sent for {symbol} {interval}")
                elif interval == "1d":
                    logging.info(f"Trailing SL for {symbol} 1D: {result['trailing_sl']} USDT")

            except Exception as e:
                logging.error(f"Error processing {symbol} {interval}: {str(e)}")

try:
    test_data = analyze("BTCUSDT", "15m")
    if test_data:
        test_msg = generate_test_alert(test_data)
        bot.send_message(chat_id=CHAT_ID, text=test_msg)
        logging.info("Test alert sent.")
        print("Test alert sent.")
    else:
        logging.info("No test data available for initial alert.")
        print("No test data available for initial alert.")
except Exception as e:
    logging.error(f"Test alert error: {e}")
    print(f"Test alert error: {e}")

scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Kolkata'))
scheduler.add_job(run_strategy, 'interval', minutes=10)
scheduler.start()

@app.route('/')
def home():
    return "Bot is running"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
