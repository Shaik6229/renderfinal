import os
import logging
from datetime import datetime, timedelta
import pytz
import requests
import pandas as pd
from flask import Flask, request
import asyncio
import nest_asyncio
from threading import Thread
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands
from ta.trend import EMAIndicator, MACD
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --------------------------
# Configuration / Constants
# --------------------------
# Define STOCH_CONFIG at module top so analyze() can access it
STOCH_CONFIG = {
    "1h": [14, 28, 56, 112],  # example windows; adjust per your strategy
    "1d": [14, 28, 56, 112],
    # add other intervals as needed
}

# Stochastic confidence thresholds: for entry, lower %K is stronger oversold
def stoch_confidence(k_value: float) -> int:
    try:
        if k_value is None or pd.isna(k_value):
            return 0
        if k_value < 20:
            return 100
        elif k_value < 30:
            return 75
        elif k_value < 50:
            return 50
        elif k_value < 70:
            return 25
        else:
            return 0
    except Exception as e:
        logging.error(f"Error in stoch_confidence: {e}")
        return 0

# Categories for market cap-based adjustments
BLUE_CHIP = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']
MID_CAP  = ['AVAXUSDT', 'DOGEUSDT', 'ADAUSDT', 'MATICUSDT', 'DOTUSDT', 'LINKUSDT', 'LTCUSDT']
# Low cap: everything else in scan list

# Retry strategy for HTTP requests
REQUESTS_RETRY_STRATEGY = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)

session = requests.Session()
adapter = HTTPAdapter(max_retries=REQUESTS_RETRY_STRATEGY)
session.mount("https://", adapter)
session.mount("http://", adapter)

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")

# Flask app to keep alive
app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive!"

@app.route('/test-alert')
def test_alert():
    secret_key = os.getenv("TEST_ALERT_KEY", "asdf")
    key = request.args.get('key')
    if key != secret_key:
        return "Unauthorized", 401
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    message = "✅ Test alert from your Crypto Alert Bot!"
    if not bot_token or not chat_id:
        return f"❌ Missing environment variables!", 500
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        resp = session.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            return "Test alert sent!"
        else:
            logging.error(f"Failed to send test alert: {resp.text}")
            return f"Failed to send test alert: {resp.text}", 500
    except Exception as e:
        logging.error(f"Error sending test alert: {e}")
        return f"Error sending test alert: {e}", 500

# Run Flask app
def run():
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))

# Trackers
highs_tracker = {}
alert_tracker = {}

# Fetch OHLCV with retries and error handling
def fetch_ohlcv(symbol, interval, limit=500):
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}'
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        df = pd.DataFrame(data, columns=['open_time', 'open', 'high', 'low', 'close', 'volume',
                                         'close_time', 'quote_asset_volume', 'trades',
                                         'taker_buy_base', 'taker_buy_quote', 'ignore'])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        df[numeric_cols] = df[numeric_cols].astype(float)
        return df
    except Exception as e:
        logging.error(f"Error fetching OHLCV for {symbol} {interval}: {e}")
        return pd.DataFrame()

# Suppression: narrow Bollinger band
def is_suppressed(df):
    if df.empty or len(df) < 20:
        return True
    try:
        bb = BollingerBands(df['close'], window=20, window_dev=2)
        width = bb.bollinger_hband() - bb.bollinger_lband()
        avg_width = width.rolling(window=20).mean().iloc[-1]
        return avg_width < 0.01 * df['close'].iloc[-1]
    except Exception as e:
        logging.error(f"Error calculating suppression: {e}")
        return True

# EMA
def fetch_ema(df, length=200):
    try:
        return EMAIndicator(df['close'], length).ema_indicator().iloc[-1]
    except Exception as e:
        logging.error(f"Error calculating EMA: {e}")
        return None

# Trend check
def check_trend(symbol, interval):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 200:
        return False
    ema_200 = fetch_ema(df, 200)
    if ema_200 is None:
        return False
    return df['close'].iloc[-1] > ema_200

# Market cap category
def categorize_by_mcap(symbol):
    if symbol in BLUE_CHIP:
        return "Blue Chip"
    elif symbol in MID_CAP:
        return "Mid Cap"
    else:
        return "Low Cap"

# Volume spike with dynamic thresholds
def volume_spike(df, symbol):
    if df.empty or len(df) < 20:
        return False
    vol = df['volume'].iloc[-20:]
    mean_vol = vol.mean()
    std_vol = vol.std()
    current_vol = df['volume'].iloc[-1]
    category = categorize_by_mcap(symbol)
    if category == "Low Cap":
        multiplier = 1.2
    elif category == "Mid Cap":
        multiplier = 1.5
    else:  # Blue Chip
        multiplier = 2.0
    return current_vol > mean_vol + multiplier * std_vol

# RSI divergence detection
def rsi_divergence(df):
    try:
        rsi_vals = RSIIndicator(df['close'], window=14).rsi().iloc[-15:]
        lows_price = df['low'].iloc[-15:]
        if len(rsi_vals) < 14 or len(lows_price) < 14:
            return False
        # Find two most recent lows
        price_lows_idx = lows_price.nsmallest(2).index.tolist()
        if len(price_lows_idx) < 2:
            return False
        first, second = price_lows_idx[0], price_lows_idx[1]
        price_condition = lows_price.loc[first] > lows_price.loc[second]
        rsi_condition = rsi_vals.loc[first] < rsi_vals.loc[second]
        return price_condition and rsi_condition
    except Exception as e:
        logging.error(f"Error detecting RSI divergence: {e}")
        return False

# Time helper
def get_time():
    tz = pytz.timezone("Asia/Kolkata")
    return datetime.now(tz).strftime("%Y-%m-%d %I:%M:%S %p")

# Interpret confidence
def interpret_confidence(conf):
    if conf >= 85:
        return f"{conf}% ✅ *Strong setup* — consider full position"
    elif conf >= 70:
        return f"{conf}% ⚡ *Decent setup* — consider half position"
    elif conf >= 60:
        return f"{conf}% 🟣 *Possible opportunity* — small size or wait for confirmation"
    else:
        return f"{conf}% ❌ *Not strong* — avoid or wait"

# Entry message
def entry_msg(data):
    category = categorize_by_mcap(data['symbol'])
    suggestion = interpret_confidence(data['final_entry_confidence'])
    return f"""🟢 *[ENTRY]* — {data['symbol']} ({data['interval']}) [{category}] \
*Final confidence:* {suggestion}\
RSI: {data['rsi']:.2f} | Stochastic %K: {data['stoch_k']:.2f} / %D: {data['stoch_d']:.2f}\
Volume Spike: {"✅" if data['volume_spike'] else "❌"}\
Suppression: {"Yes ❌" if data['suppressed'] else "No ✅"}\
Divergence: {"Yes ✅" if data['divergence'] else "No ❌"}\
Trend: {"Bullish ✅" if data['trend'] else "Bearish ❌"}\
Initial SL: {data['initial_sl']:.8f} | Take‑profit: {data['bb_upper']:.8f} | TSL: {data.get('tsl_level', 'N/A')} \
Current price: {data['price']:.8f} | Time: {get_time()}"""

# TP message
def tp_msg(data):
    return f"""🟣 *[TAKE-PROFIT]* — {data['symbol']}\
RSI: {data['rsi']:.2f} | Stochastic %K: {data['stoch_k']:.2f} / %D: {data['stoch_d']:.2f}\
Volume Spike: {"✅" if data['volume_spike'] else "❌"}\
Suppression: {"Yes ❌" if data['suppressed'] else "No ✅"}\
Divergence: {"Yes ✅" if data['divergence'] else "No ❌"}\
Trend: {"Bullish ✅" if data['trend'] else "Bearish ❌"}\
Take‑profit at: {data['bb_upper']:.8f} (Final TP confidence: {data['final_tp_confidence']}%)\
Current price: {data['price']:.8f} | Time: {get_time()}"""

# Async Telegram send using to_thread to avoid blocking
async def send_telegram_message(bot_token, chat_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        def post(): return session.post(url, data=data, timeout=10)
        resp = await asyncio.to_thread(post)
        if resp.status_code != 200:
            logging.error(f"Telegram error {resp.status_code}: {resp.text}")
        return resp.json()
    except Exception as e:
        logging.error(f"Telegram send error: {e}")
        return None

# Cooldown logic
def alert_cooldown_passed(symbol, interval, kind, cooldown_minutes):
    key = f"{symbol}_{interval}_{kind}"
    now = datetime.utcnow()
    last = alert_tracker.get(key)
    if last is None or (now - last) > timedelta(minutes=cooldown_minutes):
        alert_tracker[key] = now
        return True
    return False

# Analysis logic
async def analyze(symbol, interval, tsl_percent):
    # Ensure STOCH_CONFIG is defined
    if 'STOCH_CONFIG' not in globals():
        logging.error("STOCH_CONFIG is not defined in analyze scope")
        return None
    try:
        df = await asyncio.to_thread(fetch_ohlcv, symbol, interval)
        if df.empty or len(df) < 100:
            logging.warning(f"{symbol} {interval}: insufficient data")
            return None

        # Indicators
        trend = await asyncio.to_thread(check_trend, symbol, interval)
        vol_spike = await asyncio.to_thread(volume_spike, df, symbol)
        suppressed = await asyncio.to_thread(is_suppressed, df)
        divergence = await asyncio.to_thread(rsi_divergence, df)
        rsi = RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]

        # Four Stochastics
        windows = STOCH_CONFIG.get(interval, [])
        stoch_results = []
        for w in windows:
            try:
                st = StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=w, smooth_window=3)
                k = st.stoch().iloc[-1]
                d = st.stoch_signal().iloc[-1]
            except Exception as e:
                logging.error(f"Error Stochastic for {symbol} {interval} window {w}: {e}
"
