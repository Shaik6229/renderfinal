# === main.py ===
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
from threading import Thread

# === Logging ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")

# === Flask Server (Keep Alive + Test) ===
app = Flask('')

@app.route("/", methods=["GET", "HEAD"])
def home():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"📡 UptimeRobot ping received at {now}")
    return "✅ Bot is running", 200

@app.route('/test-alert')
def test_alert():
    if request.headers.get('User-Agent', '').lower().find('uptimerobot') != -1:
        logging.info("⏸️ Skipping test alert — UptimeRobot ping.")
        return "Ping received from UptimeRobot", 200

    if request.args.get('key') != "asdf":
        return "Unauthorized", 401

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        return "❌ Missing Telegram environment variables", 500

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {'chat_id': chat_id, 'text': "✅ Test alert from your Crypto Alert Bot!", 'parse_mode': 'Markdown'}

    resp = requests.post(url, data=data)
    return "Test alert sent!" if resp.status_code == 200 else f"Error: {resp.text}", resp.status_code

# === Globals ===
alert_tracker = {}

# === Utility Functions ===
def fetch_ohlcv(symbol, interval, limit=500):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        data = requests.get(url).json()
        df = pd.DataFrame(data, columns=['open_time','open','high','low','close','volume','ct','qav','t','tb','tq','i'])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        for col in ['open','high','low','close','volume']:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        logging.error(f"[{symbol} - {interval}] Failed OHLCV fetch: {e}")
        return pd.DataFrame()

def is_suppressed(df):
    if df.empty or len(df) < 220: return True
    try:
        bb = BollingerBands(df['close'], window=200, window_dev=2)
        width = bb.bollinger_hband() - bb.bollinger_lband()
        avg_width = width.rolling(20).mean().iloc[-1]
        return avg_width < 0.01 * df['close'].iloc[-1]
    except: return True

def fetch_ema(df, length=200):
    return EMAIndicator(df['close'], length).ema_indicator().iloc[-1]

def check_trend(symbol, interval):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 200: return False
    return df['close'].iloc[-1] > fetch_ema(df)

def volume_spike(df, symbol):
    recent_vol = df['volume'].iloc[-20:]
    mult = 1.2 if symbol in ['CVCUSDT','CTSIUSDT'] else 1.5
    return recent_vol.iloc[-1] > recent_vol.mean() + mult * recent_vol.std()

def rsi_divergence(df):
    try:
        rsi = RSIIndicator(df['close']).rsi().iloc[-15:]
        lows = df['low'].iloc[-15:]
        idx = lows.nsmallest(2).index.tolist()
        if len(idx) < 2: return False
        return lows.loc[idx[0]] > lows.loc[idx[1]] and rsi.loc[idx[0]] < rsi.loc[idx[1]]
    except: return False

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
    return f"{conf}% ❌ *Low confidence* — better to skip"

def categorize_by_mcap(symbol):
    if symbol in ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT']:
        return "Blue Chip"
    elif symbol in ['AVAXUSDT','DOGEUSDT','ADAUSDT','MATICUSDT','DOTUSDT','LINKUSDT','LTCUSDT']:
        return "Mid Cap"
    return "Low Cap"

# === Telegram Messaging ===
async def send_telegram_message(bot_token, chat_id, message):
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'}
        r = requests.post(url, data=data)
        if r.status_code != 200:
            logging.error(f"Telegram Error: {r.text}")
    except Exception as e:
        logging.error(f"Telegram send error: {e}")

def alert_cooldown_passed(symbol, interval, kind, cooldown_minutes):
    key = f"{symbol}_{interval}_{kind}"
    now = datetime.utcnow()
    last = alert_tracker.get(key)
    if last is None or (now - last) > timedelta(minutes=cooldown_minutes):
        alert_tracker[key] = now
        return True
    return False

def confidence_tag(conf):
    if conf >= 85:
        return "✅ *Strong setup*"
    elif conf >= 70:
        return "⚠️ *Decent setup*"
    elif conf >= 50:
        return "🧪 *Weak setup*"
    return "❌ *Low confidence*"

# === Alert Message Builders ===
def entry_msg(data):
    ist_time = get_time()
    utc_time = datetime.utcnow().strftime("%d-%b-%Y %H:%M")
    tsl_pct = round((1 - data['tsl_level'] / data['highest']) * 100, 2)
    htf_label = "1D" if data['interval'] in ["1h", "4h"] else "1W"

    return f"""
🚀 ENTRY SIGNAL — {data['symbol']} @ ${data['price']} ({data['interval']})

📈 Reasons:
• {'✅' if data['macd_bullish'] else '❌'} MACD Histogram: {'Green & rising' if data['macd_bullish'] else 'Weak or flat'}
• {'✅' if data['rsi'] < 35 else '❌'} RSI: {'Rebounding from oversold (RSI = ' + str(data['rsi']) + ')' if data['rsi'] < 35 else 'Neutral/High (RSI = ' + str(data['rsi']) + ')'}
• {'✅' if data['stoch_k'] < 30 and data['stoch_d'] < 30 else '❌'} Stochastic Oversold (K: {data['stoch_k']}, D: {data['stoch_d']})
• {'✅' if data['volume_spike'] else '❌'} Volume Spike detected
• {'✅' if data['htf_trend'] else '❌'} HTF Trend ({htf_label}): {'Bullish' if data['htf_trend'] else 'Bearish'}
• {'✅' if not data['suppressed'] else '❌'} Suppression: {'No' if not data['suppressed'] else 'Yes'}
• {'✅' if data['divergence'] else '❌'} Divergence: {'Bullish RSI Divergence' if data['divergence'] else 'None'}

🎯 Confidence Score: {data['confidence']}% — {confidence_tag(data['confidence'])}
🛡️ Suggested TSL: {tsl_pct}%

🕒 UTC: {utc_time}  
🕒 IST: {ist_time}
""".strip()


def tp_msg(data):
    ist_time = get_time()
    utc_time = datetime.utcnow().strftime("%d-%b-%Y %H:%M")
    confidence = data['tp_conf']
    tsl_pct = round((1 - data['tsl_level'] / data['highest']) * 100, 2)
    htf_label = "1D" if data['interval'] in ["1h", "4h"] else "1W"

    return f"""
🎯 TAKE PROFIT SIGNAL — {data['symbol']} @ ${data['price']} ({data['interval']})

📉 Reasons:
• {'✅' if data['macd_line'] < data['macd_signal'] else '❌'} MACD Histogram: {'Turning red' if data['macd_line'] < data['macd_signal'] else 'Still bullish'}
• {'✅' if data['rsi'] > 70 else '❌'} RSI Overbought (RSI = {data['rsi']})
• {'✅' if data['stoch_k'] > 80 and data['stoch_d'] > 80 else '❌'} Stochastic Overbought (K: {data['stoch_k']}, D: {data['stoch_d']})
• {'❌' if data['volume_spike'] else '✅'} Volume Weakening
• {'✅' if data['price'] >= data['bb_upper'] else '❌'} Resistance Zone (Upper BB hit)
• {'✅' if data['htf_trend'] else '❌'} HTF Trend ({htf_label}): {'Still Bullish (be cautious)' if data['htf_trend'] else 'Bearish'}

🎯 Confidence Score: {confidence}% — {confidence_tag(confidence)}
🛡️ Suggested TSL: {tsl_pct}%

🕒 UTC: {utc_time}  
🕒 IST: {ist_time}
""".strip()

# === Analysis Logic ===
def analyze(symbol, interval, tsl_percent):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 220:
        return None

    try:
        rsi = RSIIndicator(df['close']).rsi().iloc[-1]

        macd = MACD(
            df['close'],
            window_slow=200,
            window_fast=100,
            window_sign=50
        )
        macd_line = macd.macd().iloc[-1]
        macd_signal = macd.macd_signal().iloc[-1]
        macd_hist = macd.macd_diff().iloc[-1]
        macd_bullish = macd_line > macd_signal

        stoch = StochasticOscillator(df['high'], df['low'], df['close'])
        stoch_k = stoch.stoch().iloc[-1]
        stoch_d = stoch.stoch_signal().iloc[-1]

        bb = BollingerBands(df['close'], window=200, window_dev=2)
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]
        price = df['close'].iloc[-1]

        trend = check_trend(symbol, interval)
        htf_trend = check_trend(symbol, "1d") if interval in ["1h", "4h"] else check_trend(symbol, "1w")
        suppressed = is_suppressed(df)
        volume_spike_ = volume_spike(df, symbol)
        divergence = rsi_divergence(df)

        confidence = 0
        confidence += 25 if htf_trend else 0
        confidence += 20 if trend else 0
        confidence += 20 if volume_spike_ else 0
        confidence += 10 if not suppressed else 0
        confidence += 15 if divergence else 0
        confidence += 10 if price <= bb_lower else 0
        confidence += 10 if rsi < 30 else 0
        confidence += 10 if stoch_k < 20 and stoch_d < 20 else 0
        confidence += 15 if macd_bullish else 0
        normalized_conf = round((confidence / 135) * 100, 2)

        # --- TP Confidence Logic ---
        tp_confidence = 0
        tp_confidence += 25 if rsi > 70 else 0
        tp_confidence += 25 if stoch_k > 80 and stoch_d > 80 else 0
        tp_confidence += 25 if price >= bb_upper else 0
        tp_confidence += 15 if macd_line < macd_signal else 0
        tp_confidence += 10 if not volume_spike_ else 0  # Weakening volume
        tp_conf = round((tp_confidence / 100) * 100, 2)

        tp = tp_conf >= 60

        return {
            'symbol': symbol,
            'interval': interval,
            'confidence': normalized_conf,
            'rsi': round(rsi, 2),
            'stoch_k': round(stoch_k, 2),
            'stoch_d': round(stoch_d, 2),
            'price': round(price, 4),
            'bb_upper': round(bb_upper, 4),
            'bb_lower': round(bb_lower, 4),
            'trend': trend,
            'htf_trend': htf_trend,
            'suppressed': suppressed,
            'volume_spike': volume_spike_,
            'divergence': divergence,
            'initial_sl': round(df['low'].iloc[-5:].min(), 4),
            'highest': round(df['high'].max(), 4),
            'tsl_level': round(df['high'].max() * (1 - tsl_percent), 4),
            'macd_line': round(macd_line, 4),
            'macd_signal': round(macd_signal, 4),
            'macd_hist': round(macd_hist, 4),
            'macd_bullish': macd_bullish,
            'entry': normalized_conf >= 50,
            'tp': tp,
            'tp_conf': tp_conf
        }

    except Exception as e:
        logging.error(f"Analysis error {symbol} {interval}: {e}")
        return None

# === Bot Loop ===
async def scan_symbols():
    pairs = [
    "1INCHUSDT", "AAVEUSDT", "ACHUSDT", "ADAUSDT", "AGIXUSDT", "ALGOUSDT", "ALICEUSDT", "APTUSDT", "ARBUSDT",
    "ARUSDT", "ATOMUSDT", "AVAXUSDT", "BANDUSDT", "BNBUSDT", "CELOUSDT", "CKBUSDT", "COTIUSDT", "CFXUSDT",
    "CVCUSDT", "CTKUSDT", "CTSIUSDT", "DAIUSDT", "DIAUSDT", "DOGEUSDT", "DOTUSDT", "DYDXUSDT", "EGLDUSDT",
    "ENSUSDT", "EOSUSDT", "ETHUSDT", "FETUSDT", "FILUSDT", "FLUXUSDT", "GALAUSDT", "GLMRUSDT", "GRTUSDT",
    "HOTUSDT", "ICPUSDT", "ICXUSDT", "IDUSDT", "ILVUSDT", "INJUSDT", "JOEUSDT", "KAVAUSDT", "LINKUSDT",
    "LITUSDT", "LPTUSDT", "LTCUSDT", "MANAUSDT", "MATICUSDT", "MIOTAUSDT", "MKRUSDT", "NEARUSDT", "OCEANUSDT",
    "ONEUSDT", "ONTUSDT", "OPUSDT", "PHBUSDT", "QNTUSDT", "RENUSDT", "RLCUSDT", "RNDRUSDT", "ROSEUSDT",
    "RLYUSDT", "SANDUSDT", "SFPUSDT", "SOLUSDT", "STMXUSDT", "STORJUSDT", "STXUSDT", "SUPERUSDT", "SUIUSDT",
    "TAOUSDT", "TELUSDT", "TONUSDT", "TRXUSDT", "UNFIUSDT", "UTKUSDT", "VETUSDT", "VICUSDT", "XEMUSDT",
    "XLMUSDT", "XMRUSDT", "XRPUSDT", "XTZUSDT", "YGGUSDT", "ZILUSDT"
    ]


    intervals = {"4h": 60, "1d": 180}
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")


    for symbol in pairs:
        for tf, cooldown in intervals.items():  # <-- This line must be indented
            data = analyze(symbol, tf, 0.25)
            if not data:
                continue

            logging.info(f"⏳ Checked {symbol} {tf} — Confidence: {data['confidence']}% — Entry: {data['entry']} — TP: {data['tp']}")

            if data['entry'] and alert_cooldown_passed(symbol, tf, "entry", cooldown):
                await send_telegram_message(bot_token, chat_id, entry_msg(data))
                logging.info(f"✅ Entry alert: {symbol} {tf} ({data['confidence']}%)")

            if data['tp'] and alert_cooldown_passed(symbol, tf, "tp", cooldown):
                await send_telegram_message(bot_token, chat_id, tp_msg(data))
                logging.info(f"🎯 TP alert: {symbol} {tf}")



async def main_loop():
    while True:
        await scan_symbols()
        await asyncio.sleep(1800)

def run():
    port = int(os.environ['PORT'])
    app.run(host='0.0.0.0', port=port)


if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    Thread(target=run).start()
    Thread(target=lambda: asyncio.run(main_loop())).start()
