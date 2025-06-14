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
from ta.trend import EMAIndicator

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
        return f"{conf}% ⚠️ *Decent setup* — consider half position"
    elif conf >= 50:
        return f"{conf}% 🧪 *Weak setup* — small size or wait"
    else:
        return f"{conf}% ❌ *Low confidence* — better to skip"

def entry_msg(data):
    category = categorize_by_mcap(data['symbol'])
    suggestion = interpret_confidence(data['confidence'])
    return f"""
🟢 *[ENTRY]* — {data['symbol']} ({data['interval']}) [{category}]
*Confidence:* {suggestion}
RSI: {data['rsi']} | Stoch %K: {data['stoch_k']} / %D: {data['stoch_d']}
Price at Lower BB ✅ | Volume Spike {'✅' if data['volume_spike'] else '❌'} | Trend: {'Bullish ✅' if data['trend'] else '❌'}
Suppression: {'Yes ❌' if data['suppressed'] else 'No ✅'} | RSI Divergence: {'Yes ✅' if data['divergence'] else 'No ❌'}
Initial SL: {data['initial_sl']}
TP Target: {data['bb_upper']} | Suggested TSL: {data['tsl_level']} (Trail {round((1 - data['tsl_level']/data['highest']) * 100, 2)}%)
Price: {data['price']} | Time: {get_time()}
"""

def tp_msg(data):
    category = categorize_by_mcap(data['symbol'])
    confidence = 0
    confidence += 25 if data['rsi'] > 70 else 0
    confidence += 25 if data['stoch_k'] > 80 and data['stoch_d'] > 80 else 0
    confidence += 25 if data['price'] >= data['bb_upper'] else 0
    confidence += 25 if not data['suppressed'] else 0
    confidence = min(confidence, 100)
    suggestion = interpret_confidence(confidence)
    return f"""
🟡 *[TAKE PROFIT]* — {data['symbol']} ({data['interval']}) [{category}]
*Confidence:* {suggestion}
Price near Upper BB ✅ | RSI: {data['rsi']} | Stoch %K: {data['stoch_k']} / %D: {data['stoch_d']}
Price: {data['price']} | Time: {get_time()}
"""

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
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 100:
        return None

    try:
        rsi = RSIIndicator(df['close']).rsi().iloc[-1]
        stoch = StochasticOscillator(df['high'], df['low'], df['close'], window=14)
        stoch_k = stoch.stoch().iloc[-1]
        stoch_d = stoch.stoch_signal().iloc[-1]
        bb = BollingerBands(df['close'])
        bb_lower = bb.bollinger_lband().iloc[-1]
        bb_upper = bb.bollinger_hband().iloc[-1]
        price = df['close'].iloc[-1]
        trend = check_trend(symbol, interval)
        suppressed = is_suppressed(df)
        vol_spike = volume_spike(df, symbol)
        divergence = rsi_divergence(df)
        entry = (price <= bb_lower) and (rsi < 35) and (stoch_k < 30 and stoch_d < 30) and trend and not suppressed and vol_spike
        tp = (price >= bb_upper) and (rsi > 70 or (stoch_k > 80 and stoch_d > 80))
        highest = df['high'].max()
        tsl_level = highest * (1 - tsl_percent)
        initial_sl = df['low'].iloc[-5:].min()
        confidence = 0
        confidence += 20 if trend else 0
        confidence += 20 if vol_spike else 0
        confidence += 20 if not suppressed else 0
        confidence += 20 if divergence else 0
        confidence += 20 if entry else 0
        return {
            'symbol': symbol,
            'interval': interval,
            'entry': entry,
            'tp': tp,
            'confidence': confidence,
            'rsi': round(rsi, 2),
            'stoch_k': round(stoch_k, 2),
            'stoch_d': round(stoch_d, 2),
            'price': round(price, 4),
            'bb_upper': round(bb_upper, 4),
            'bb_lower': round(bb_lower, 4),
            'trend': trend,
            'suppressed': suppressed,
            'volume_spike': vol_spike,
            'divergence': divergence,
            'initial_sl': round(initial_sl, 4),
            'highest': round(highest, 4),
            'tsl_level': round(tsl_level, 4),
        }
    except Exception as e:
        logging.error(f"Error analyzing {symbol} {interval}: {e}")
        return None

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
                elif data['tp'] and alert_cooldown_passed(symbol, interval, 'tp', cooldown):
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
    Thread(target=run).start()
    Thread(target=start_bot).start()
