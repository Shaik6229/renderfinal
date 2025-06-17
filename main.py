import os
import logging
from datetime import datetime, timedelta
import pytz
import requests
import pandas as pd
from flask import Flask, request
import asyncio
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands
from ta.trend import EMAIndicator, MACD

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")

# Flask app to keep alive
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

@app.route('/test-alert')
def test_alert():
    secret_key = "asdf"
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
        resp = requests.post(url, data=data)
        if resp.status_code == 200:
            return "Test alert sent!"
        else:
            logging.error(f"Failed to send test alert: {resp.text}")
            return f"Failed to send test alert: {resp.text}", 500
    except Exception as e:
        logging.error(f"Error sending test alert: {e}")
        return f"Error sending test alert: {e}", 500

def run():
    app.run(host='0.0.0.0', port=8080)

highs_tracker = {}
alert_tracker = {}

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
        logging.error(f"Error fetching OHLCV for {symbol} {interval}: {e}")
        return pd.DataFrame()

def is_suppressed(df):
    if df.empty or len(df) < 20:
        return True
    try:
        bb = BollingerBands(df['close'])
        width = bb.bollinger_hband() - bb.bollinger_lband()
        avg_width = width.rolling(window=20).mean().iloc[-1]
        return avg_width < 0.01 * df['close'].iloc[-1]
    except Exception as e:
        logging.error(f"Error calculating suppression: {e}")
        return True

def fetch_ema(df, length=200):
    return EMAIndicator(df['close'], length).ema_indicator().iloc[-1]

def check_trend(symbol, interval):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 200:
        return False
    ema_200 = fetch_ema(df, 200)
    return df['close'].iloc[-1] > ema_200

def volume_spike(df, symbol):
    vol = df['volume'].iloc[-20:]
    mean_vol = vol.mean()
    std_vol = vol.std()
    current_vol = df['volume'].iloc[-1]

    # Adjust threshold based on symbol category
    low_cap_symbols = ['CVCUSDT', 'CTSIUSDT', 'BANDUSDT', 'KAVAUSDT', 'FLUXUSDT', 'SFPUSDT', 'ILVUSDT', 'AGIXUSDT']
    multiplier = 1.2 if symbol in low_cap_symbols else 1.5

    return current_vol > mean_vol + multiplier * std_vol

def rsi_divergence(df):
    try:
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
    except Exception as e:
        logging.error(f"Error detecting RSI divergence: {e}")
        return False

def categorize_by_mcap(symbol):
    blue_chip = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']
    mid_cap = ['AVAXUSDT', 'DOGEUSDT', 'ADAUSDT', 'MATICUSDT', 'DOTUSDT', 'LINKUSDT', 'LTCUSDT']
    if symbol in blue_chip:
        return "Blue Chip"
    elif symbol in mid_cap:
        return "Mid Cap"
    else:
        return "Low Cap"

def get_time():
    tz = pytz.timezone("Asia/Kolkata")
    return datetime.now(tz).strftime("%Y-%m-%d %I:%M:%S %p")

def interpret_confidence(conf):
    if conf >= 85:
        return f"{conf}% ✅ *Strong setup* — consider full position"
    elif conf >= 70:
        return f"{conf}% ⚡ *Decent setup* — consider half position"
    elif conf >= 60:
        return f"{conf}% 🟣 *Possible opportunity* — small size or wait for confirmation"
    else:
        return f"{conf}% ❌ *Not strong* — avoid or wait"

def entry_msg(data):
    category = categorize_by_mcap(data['symbol'])

    suggestion = interpret_confidence(data['entry_confidence'])

    return f"""🟢 *[ENTRY]* — {data['symbol']} ({data['interval']}) [{category}] 
*Entry confidence:* {suggestion}
RSI: {data['rsi']} | Stochastic %K: {data['stoch_k']} / %D: {data['stoch_d']}
Volume Spike: {"✅" if data['volume_spike'] else "❌"}
Suppression: {"Yes ❌" if data['suppressed'] else "No ✅"}
Divergence: {"Yes ✅" if data['divergence'] else "No ❌"}
Trend: {"Bullish ✅" if data['trend'] else "Bearish ❌"}
Initial SL: {data['initial_sl']} | Take-profit: {data['bb_upper']} | TSL: {data['tsl_level']} 
Current price: {data['price']} | Time: {get_time()}"""


def tp_msg(data):
    return f"""🟣 *[TAKE-PROFIT]* — {data['symbol']} 
RSI: {data['rsi']} | Stochastic %K: {data['stoch_k']} / %D: {data['stoch_d']}
Volume Spike: {"✅" if data['volume_spike'] else "❌"}
Suppression: {"Yes ❌" if data['suppressed'] else "No ✅"}
Divergence: {"Yes ✅" if data['divergence'] else "No ❌"}
Trend: {"Bullish ✅" if data['trend'] else "Bearish ❌"}
Take-profit at: {data['bb_upper']} (Take-profit confidence: {data['take_profit_confidence']}%)
Current price: {data['price']} | Time: {get_time()}"""



async def send_telegram_message(bot_token, chat_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        resp = requests.post(url, data=data)
        if resp.status_code != 200:
            logging.error(f"Telegram error {resp.status_code}: {resp.text}")
        return resp.json()
    except Exception as e:
        logging.error(f"Telegram send error: {e}")
        return None

def alert_cooldown_passed(symbol, interval, kind, cooldown_minutes):
    key = f"{symbol}_{interval}_{kind}"
    now = datetime.utcnow()
    last = alert_tracker.get(key)
    if last is None or (now - last) > timedelta(minutes=cooldown_minutes):
        alert_tracker[key] = now
        return True
    return False

def analyze(symbol, interval, tsl_percent):
    try:
        df = fetch_ohlcv(symbol, interval)
        if df.empty or len(df) < 100:
            logging.warning(f"{symbol} {interval}: insufficient data")
            return {
                "entry": False,
                "entry_confidence": 0,
                "take_profit_confidence": 0,
                "take_profit": False,
                "entry_price": None,
                "initial_stoploss": None,
                "macd_cross_up": False,
                "higher_tf_conf": 0,
                "note": "Insufficient data"
            }

        # Perform Indicator Calculations
        trend = check_trend(symbol, interval)
        vol_spike = volume_spike(df, symbol)
        suppressed = is_suppressed(df)
        divergence = rsi_divergence(df)
        rsi = RSIIndicator(close=df['close']).rsi().iloc[-1]
        stoch = StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14)
        stoch_k = stoch.stochastic_k().iloc[-1]
        stoch_d = stoch.stochastic_d().iloc[-1]
        bb = BollingerBands(close=df['close'], window=20, window_dev=2)
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]

        # Define price first
        price = df['close'].iloc[-1]

        # Then compute initial_sl
        initial_sl = price * (1 - 0.05)

        # MACD Indicator
        macd = MACDIndicator(close=df['close'], window_slow=26, window_fast=12, window_signal=9)
        macd_line = macd.macd().iloc[-1]
        signal_line = macd.signal().iloc[-1]
        prev_macd = macd.macd().iloc[-2]
        prev_signal = macd.signal().iloc[-2]
        macd_cross_up = macd_line > signal_line and prev_macd <= prev_signal

        # Higher timeframe confirmation (1D)
        df_1d = fetch_ohlcv(symbol, "1d")
        higher_tf_conf = 0
        if df_1d is not None and len(df_1d) > 100:
            macd_1d = MACDIndicator(close=df_1d['close'], window_slow=26, window_fast=12, window_signal=9)
            macd_line_1d = macd_1d.macd().iloc[-1]
            signal_line_1d = macd_1d.signal().iloc[-1]
            if macd_line_1d > signal_line_1d:
                higher_tf_conf = 10

        # Define entry condition
        entry = price < bb_lower

        # Normalized entry confidence weights
        weights = {
            "trend": 15,
            "vol_spike": 10,
            "not_suppressed": 10,
            "divergence": 10,
            "price_below_bb_lower": 10,
            "macd_cross_up": 25,
            "higher_tf_conf": 20,
        }

        entry_confidence = 0
        if trend:
            entry_confidence += weights["trend"]
        if vol_spike:
            entry_confidence += weights["vol_spike"]
        if not suppressed:
            entry_confidence += weights["not_suppressed"]
        if divergence:
            entry_confidence += weights["divergence"]
        if entry:
            entry_confidence += weights["price_below_bb_lower"]
        if macd_cross_up:
            entry_confidence += weights["macd_cross_up"]
        if higher_tf_conf == 10:
            entry_confidence += weights["higher_tf_conf"]

        entry_confidence = min(entry_confidence, 100)

        if entry_confidence < 50:
            entry = False

        # Take-profit confidence weights
        tp_confidence = 0
        if rsi > 70:
            tp_confidence += 25
        if stoch_k > 80 and stoch_d > 80:
            tp_confidence += 25
        if price >= bb_upper:
            tp_confidence += 25
        if not suppressed:
            tp_confidence += 25

        tp_confidence = min(tp_confidence, 100)
        tp = tp_confidence >= 50

        return {
            "entry": entry,
            "entry_confidence": entry_confidence,
            "take_profit_confidence": tp_confidence,
            "take_profit": tp,
            "entry_price": price,
            "initial_stoploss": initial_sl,
            "macd_cross_up": macd_cross_up,
            "higher_tf_conf": higher_tf_conf
        }

    except Exception as e:
        logging.error(f"Error analyzing {symbol}: {e}")
        return {
            "entry": False,
            "entry_confidence": 0,
            "take_profit_confidence": 0,
            "take_profit": False,
            "entry_price": None,
            "initial_stoploss": None,
            "macd_cross_up": False,
            "higher_tf_conf": 0,
            "note": f"Exception: {e}"
        }


async def scan_symbols():
    pairs = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "AVAXUSDT", "DOTUSDT", "MATICUSDT", "NEARUSDT", "ATOMUSDT",
        "LTCUSDT", "LINKUSDT", "BCHUSDT", "EGLDUSDT", "XLMUSDT",
        "FILUSDT", "APTUSDT", "OPUSDT", "ARBUSDT", "INJUSDT",
        "FETUSDT", "RNDRUSDT", "ARUSDT", "GRTUSDT", "STXUSDT",
        "CVCUSDT", "CTSIUSDT", "BANDUSDT", "CFXUSDT", "KAVAUSDT",
        "ENSUSDT", "FLUXUSDT", "SFPUSDT", "ILVUSDT", "AGIXUSDT",
        "OCEANUSDT", "DYDXUSDT", "MKRUSDT", "IDUSDT", "TAOUSDT"
    ]
    intervals = {"1h": 30, "1d": 360}
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    for symbol in pairs:
        for interval, cooldown in intervals.items():
            try:
                data = analyze(symbol, interval, 0.25 if interval == "1h" else 0.35)
                if not data:
                    continue

                if data['entry'] and alert_cooldown_passed(symbol, interval, 'entry', cooldown):
                    msg = entry_msg(data)
                    await send_telegram_message(bot_token, chat_id, msg)
                elif (data['take_profit'] 
                      and data.get('take_profit_confidence', 0) > 60 
                      and alert_cooldown_passed(symbol, interval, 'tp', cooldown)):
                    msg = tp_msg(data)
                    await send_telegram_message(bot_token, chat_id, msg)

            except Exception as e:
                logging.error(f"Scan error for {symbol} {interval}: {e}")


async def main_loop():
    while True:
        await scan_symbols()
        await asyncio.sleep(1800)

# --- FINAL EXECUTION BLOCK ---
from threading import Thread

def start_bot():
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main_loop())

if __name__ == '__main__':
    # Run the Flask app in a separate thread
    Thread(target=run).start()
    # Run your bot’s main loop in another thread
    Thread(target=start_bot).start()

