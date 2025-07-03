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
    "XLMUSDT", "XMRUSDT", "XRPUSDT", "XTZUSDT", "YGGUSDT", "ZILUSDT", "SAHARAUSDT", "NEWTUSDT",
    ]

# === Timeframe-Specific Config ===
TIMEFRAME_CONFIG = {
    "30m": {
        "htf": "4h",
        "volume_window": 12,
        "cooldown": 30,
        "confidence_weights": {
            "htf_trend": 15, "trend": 10, "volume": 15, "macd_hist": 20,
            "stoch_crossover": 10, "ema50": 10, "divergence": 10
        },
        "tp_weights": {
            "rsi_overbought": 20,
            "stoch_overbought": 12,
            "bb_hit": 20,
            "macd_cross": 15,
            "vol_weak": 10,
            "rsi_div": 10,
            "stoch_cross": 10,
            "rejection_wick": 10,
            "htf_bear": 5
        },
        "entry_threshold": 60,  # 🔒 Stronger filters for fewer but better 30m signals
        "tp_threshold": 60,
        "tsl": 0.08             # 🔄 Tight TSL for scalps
    },
    "4h": {
        "htf": "1d",
        "volume_window": 20,
        "cooldown": 60,
        "confidence_weights": {
            "htf_trend": 25, "trend": 15, "volume": 15, "macd_hist": 15,
            "stoch_crossover": 10, "ema50": 10, "divergence": 15
        },
        "tp_weights": {
            "rsi_overbought": 25,
            "stoch_overbought": 20,
            "bb_hit": 15,
            "macd_cross": 15,
            "vol_weak": 10,
            "rsi_div": 15,
            "stoch_cross": 10,
            "rejection_wick": 5,
            "htf_bear": 8
        },
        "entry_threshold": 65,  # 🚀 Wait for more confluence on 4H
        "tp_threshold": 65,
        "tsl": 0.18             # 🧘‍♂️ Swing-safe TSL
    },
    "1d": {
        "htf": "1w",
        "volume_window": 30,
        "cooldown": 180,
        "confidence_weights": {
            "htf_trend": 30, "trend": 20, "volume": 10, "macd_hist": 15,
            "stoch_crossover": 5, "ema50": 15, "divergence": 20
        },
        "tp_weights": {
            "rsi_overbought": 30,
            "stoch_overbought": 15,
            "bb_hit": 20,
            "macd_cross": 10,
            "vol_weak": 5,
            "rsi_div": 20,
            "stoch_cross": 10,
            "rejection_wick": 10,
            "htf_bear": 10
        },
        "entry_threshold": 70,  # 🧠 Highest quality trades only
        "tp_threshold": 70,
        "tsl": 0.30             # 🛡️ Strong trend safety net
    }
}





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


symbol_volumes = {}

def fetch_24h_volumes():
    global symbol_volumes
    try:
        url = "https://api.binance.com/api/v3/ticker/24hr"
        response = requests.get(url)
        data = response.json()
        # Save only relevant symbol:volume data
        symbol_volumes = {
            item["symbol"]: float(item["quoteVolume"])
            for item in data
            if item["symbol"].endswith("USDT") and item["symbol"] in pairs
        }
    except Exception as e:
        logging.error(f"⚠️ Volume fetch error: {e}")
        symbol_volumes = {}

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

def get_max_confidence_score(interval):
    weights = TIMEFRAME_CONFIG[interval]["confidence_weights"]
    # These are static bonuses added in scoring logic — add them too
    static_bonuses = {
        "bb_lower": 10,
        "rsi_dynamic": 10,
        "stoch_oversold": 10,
        "macd_bullish": 15,
        "no_suppression": 10
    }
    penalties = {
        "rsi_neutral": -10,
        "tight_range": -10
    }
    total = sum(weights.values()) + sum(static_bonuses.values()) + abs(sum(penalties.values()))
    return total


def is_suppressed(df):
    if df.empty or len(df) < 220: return True
    try:
        bb = BollingerBands(df['close'], window=200, window_dev=2)
        width = bb.bollinger_hband() - bb.bollinger_lband()
        rolling_width = width.rolling(20)

        avg_width = rolling_width.mean().iloc[-1]
        std_width = rolling_width.std().iloc[-1]
        dynamic_threshold = avg_width - std_width

        return width.iloc[-1] < dynamic_threshold
    except: return True


def fetch_ema(df, length=200):
    return EMAIndicator(df['close'], length).ema_indicator().iloc[-1]

def check_trend(symbol, interval):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 200: return False
    return df['close'].iloc[-1] > fetch_ema(df)


def volume_spike(df, symbol, interval):
    window = TIMEFRAME_CONFIG[interval]["volume_window"]
    recent_vol = df['volume'].iloc[-window:]

    # Default multiplier
    mult = 1.5

    # Use dynamic multiplier based on overall 24h quote volume
    global symbol_volumes
    vol_24h = symbol_volumes.get(symbol, None)

    if vol_24h > 100_000_000:
        mult = 2.0
    elif vol_24h > 50_000_000:
        mult = 1.7
    elif vol_24h < 3_000_000:
        mult = 1.1
    else:
        mult = 1.4


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
    htf_label = TIMEFRAME_CONFIG[data['interval']]['htf'].upper()
    mcap = categorize_by_mcap(data['symbol'])


    return f"""
🚀 ENTRY SIGNAL — {data['symbol']} @ ${data['price']} ({data['interval']})

📂 Market Cap Category: *{mcap}*

📈 Reasons:
• {'✅' if data['macd_bullish'] else '❌'} MACD Histogram: {'Green & rising' if data['macd_bullish'] else 'Weak or flat'}
• {'✅' if data['rsi'] < 35 else '❌'} RSI: {'Rebounding from oversold (RSI = ' + str(data['rsi']) + ')' if data['rsi'] < 35 else 'Neutral/High (RSI = ' + str(data['rsi']) + ')'}
• {'✅' if data['stoch_k'] < 30 and data['stoch_d'] < 30 else '❌'} Stochastic Oversold (K: {data['stoch_k']}, D: {data['stoch_d']})
• {'✅' if data['volume_spike'] else '❌'} Volume Spike: Bullish momentum detected
• {'✅' if data['htf_trend'] else '❌'} HTF Trend ({htf_label}): {'Bullish' if data['htf_trend'] else 'Bearish'}
• {'✅' if not data['suppressed'] else '❌'} Suppression: {'No' if not data['suppressed'] else 'Yes'}
• {'✅' if data['divergence'] else '❌'} Divergence: {'Bullish RSI Divergence' if data['divergence'] else 'None'}
• {'✅' if data['macd_hist_positive'] else '❌'} MACD Momentum: {'Turning positive' if data['macd_hist_positive'] else 'Flat or negative'}
• {'✅' if data['stoch_crossover'] else '❌'} Stochastic Crossover: {'Bullish crossover' if data['stoch_crossover'] else 'No crossover'}
• {'✅' if data['ema_50'] and data['price'] > data['ema_50'] else '❌'} EMA 50: {'Price above EMA 50' if data['price'] > data['ema_50'] else 'Below EMA 50'}
• {'✅' if not data['rsi_neutral'] else '❌'} RSI Zone: {'Strong zone' if not data['rsi_neutral'] else 'Neutral RSI (40–60)'}
• {'✅' if not data['tight_range'] else '❌'} Range: {'Clear breakout potential' if not data['tight_range'] else 'Choppy sideways range'}
• {'✅' if data['price_above_vwap'] else '❌'} VWAP Check: {'Price above VWAP' if data['price_above_vwap'] else 'Below VWAP'}


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
    htf_label = TIMEFRAME_CONFIG[data['interval']]['htf'].upper()
    mcap = categorize_by_mcap(data['symbol'])  # ✅ Add this


    return f"""
🎯 TAKE PROFIT SIGNAL — {data['symbol']} @ ${data['price']} ({data['interval']})

📂 Market Cap Category: *{mcap}*

📉 Reasons:
• {'✅' if data['macd_line'] < data['macd_signal'] else '❌'} MACD Histogram: {'Turning red' if data['macd_line'] < data['macd_signal'] else 'Still bullish'}
• {'✅' if data['rsi'] > 70 else '❌'} RSI Overbought (RSI = {data['rsi']})
• {'✅' if data['stoch_k'] > 80 and data['stoch_d'] > 80 else '❌'} Stochastic Overbought (K: {data['stoch_k']}, D: {data['stoch_d']})
• {'✅' if data['volume_weakening'] else '❌'} Volume Weakening: Momentum fading
• {'✅' if data['price'] >= data['bb_upper'] else '❌'} Resistance Zone (Upper BB hit)
• {'✅' if data['htf_trend'] else '❌'} HTF Trend ({htf_label}): {'Still Bullish (be cautious)' if data['htf_trend'] else 'Bearish'}
• {'✅' if data['stoch_k'] > 80 and data['stoch_d'] > 80 else '❌'} Stochastic Overbought (K: {data['stoch_k']}, D: {data['stoch_d']})
• {'✅' if data['stoch_bear_crossover'] else '❌'} Stochastic Crossover: {'Bearish crossover' if data['stoch_bear_crossover'] else 'No crossover'}
• {'✅' if data['bearish_rsi_div'] else '❌'} RSI Divergence: {'Bearish RSI divergence' if data['bearish_rsi_div'] else 'None'}
• {'✅' if data['rejection_wick'] else '❌'} Rejection Wick: {'Long upper shadow detected' if data['rejection_wick'] else 'None'}
• {'❌' if data['volume_spike'] else '✅'} Volume Weakening
• {'✅' if data['price'] >= data['bb_upper'] else '❌'} Resistance Zone (Upper BB hit)



🎯 Confidence Score: {confidence}% — {confidence_tag(confidence)}
🛡️ Suggested TSL: {tsl_pct}%

🕒 UTC: {utc_time}  
🕒 IST: {ist_time}
""".strip()

# === Analysis Logic ===

def analyze(symbol, interval, tsl_percent=None):
    config = TIMEFRAME_CONFIG[interval]
    if tsl_percent is None:
        tsl_percent = config["tsl"]

    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 220:
        return None


    try:
        # === RSI & Smoothed RSI ===
        rsi_series = RSIIndicator(df['close']).rsi()
        rsi = rsi_series.iloc[-1]
        rsi_mean = rsi_series.rolling(14).mean().iloc[-1]
        rsi_std = rsi_series.rolling(14).std().iloc[-1]
        rsi_dynamic_threshold = rsi_mean - rsi_std
        smoothed_rsi = rsi_series.ewm(span=5).mean().iloc[-1]



        macd = MACD(
            df['close'],
            window_slow=26,
            window_fast=12,
            window_sign=9
        )
        macd_line = macd.macd().iloc[-1]
        macd_signal = macd.macd_signal().iloc[-1]
        macd_hist = macd.macd_diff().iloc[-1]
        macd_bullish = macd_line > macd_signal

        stoch = StochasticOscillator(df['high'], df['low'], df['close'])
        stoch_k = stoch.stoch().iloc[-1]
        stoch_d = stoch.stoch_signal().iloc[-1]
        # Bearish stochastic crossover (used for TP logic)
        stoch_bear_crossover = stoch.stoch().iloc[-2] > stoch.stoch_signal().iloc[-2] and stoch.stoch().iloc[-1] < stoch.stoch_signal().iloc[-1]


        bb = BollingerBands(df['close'], window=200, window_dev=2)
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]
        price = df['close'].iloc[-1]
        vwap = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
        price_above_vwap = price > vwap.iloc[-1]


        trend = check_trend(symbol, interval)
        htf_trend = check_trend(symbol, config["htf"])
        suppressed = is_suppressed(df)
        volume_spike_ = volume_spike(df, symbol, interval)
        volume_weakening = not volume_spike_
        divergence = rsi_divergence(df)

        # --- New Entry Enhancements ---
        ema_50 = EMAIndicator(df['close'], window=50).ema_indicator().iloc[-1]

        # Stochastic crossover confirmation
        stoch_crossover = stoch.stoch().iloc[-2] < stoch.stoch_signal().iloc[-2] and stoch.stoch().iloc[-1] > stoch.stoch_signal().iloc[-1]

        # MACD histogram shift
        macd_hist_positive = macd.macd_diff().iloc[-2] < 0 and macd.macd_diff().iloc[-1] > 0

        # Tight range filter (chop zone)
        tight_range = (df['close'].iloc[-10:].max() - df['close'].iloc[-10:].min()) / df['close'].iloc[-1] < 0.02

        # RSI penalty zone
        rsi_neutral = 40 < rsi < 60
                # --- Bearish RSI Divergence Detection ---
        try:
            recent_rsi = RSIIndicator(df['close']).rsi().iloc[-15:]
            recent_highs = df['high'].iloc[-15:]

            idx_highs = recent_highs.nlargest(2).index.tolist()
            bearish_rsi_div = False
            if len(idx_highs) >= 2:
                h1, h2 = idx_highs
                if recent_highs[h1] < recent_highs[h2] and recent_rsi[h1] > recent_rsi[h2]:
                    bearish_rsi_div = True
        except Exception as e:
            logging.warning(f"{symbol} {interval} - Bearish RSI div error: {e}")
            bearish_rsi_div = False

        # --- Rejection Wick Detection ---
        rejection_wick = (df['high'].iloc[-1] - df['close'].iloc[-1]) > 2 * abs(df['close'].iloc[-1] - df['open'].iloc[-1])




        # --- Updated Confidence Scoring ---
        weights = config["confidence_weights"]
        confidence = 0
        confidence += weights.get("htf_trend", 0) if htf_trend else 0
        confidence += weights.get("trend", 0) if trend else 0
        confidence += weights.get("volume", 0) if volume_spike_ else 0
        confidence += weights.get("macd_hist", 0) if macd_hist_positive else 0
        confidence += weights.get("stoch_crossover", 0) if stoch_crossover else 0
        confidence += weights.get("ema50", 0) if price > ema_50 else 0
        confidence += weights.get("divergence", 0) if divergence else 0
        confidence += 10 if price <= bb_lower else 0
        if rsi < rsi_dynamic_threshold and smoothed_rsi < rsi_dynamic_threshold:
            confidence += 10
        elif rsi < rsi_dynamic_threshold:
            confidence += 5
        elif smoothed_rsi < rsi_dynamic_threshold:
            confidence += 3
        if price <= bb_lower and rsi < 30:
            confidence += 5
        confidence += 10 if stoch_k < 20 and stoch_d < 20 else 0
        confidence += 15 if macd_bullish else 0
        confidence += 10 if not suppressed else 0
        confidence -= 10 if rsi_neutral else 0
        confidence -= 5 if tight_range else 0
        confidence += 10 if price_above_vwap else 0


        max_score = get_max_confidence_score(interval)
        normalized_conf = round((confidence / 115) * 100, 2)

        # --- TP Confidence Logic ---
        tp_weights = config["tp_weights"]
        tp_confidence = 0

        tp_confidence += tp_weights.get("rsi_overbought", 0) if rsi > 70 else 0
        tp_confidence += tp_weights.get("stoch_overbought", 0) if stoch_k > 80 and stoch_d > 80 else 0
        tp_confidence += tp_weights.get("bb_hit", 0) if price >= bb_upper else 0
        tp_confidence += tp_weights.get("macd_cross", 0) if macd_line < macd_signal else 0
        tp_confidence += tp_weights.get("vol_weak", 0) if volume_weakening else 0
        tp_confidence += tp_weights.get("rsi_div", 0) if bearish_rsi_div else 0
        tp_confidence += tp_weights.get("stoch_cross", 0) if stoch_bear_crossover else 0
        tp_confidence += tp_weights.get("rejection_wick", 0) if rejection_wick else 0
        tp_confidence += tp_weights.get("htf_bear", 0) if not htf_trend else 0
        if price >= bb_upper and rsi > 70:
            tp_confidence += 5  # You can tune this value



        tp_max_score = sum(tp_weights.values())
        tp_conf = round((tp_confidence / tp_max_score) * 100, 2)
        tp = tp_conf >= config["tp_threshold"]



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
            'volume_weakening': volume_weakening,
            'divergence': divergence,
            'initial_sl': round(df['low'].iloc[-5:].min(), 4),
            'highest': round(df['high'].max(), 4),
            'tsl_level': round(df['high'].max() * (1 - tsl_percent), 4),
            'macd_line': round(macd_line, 4),
            'macd_signal': round(macd_signal, 4),
            'macd_hist': round(macd_hist, 4),
            'macd_bullish': macd_bullish,
            'entry': normalized_conf >= config.get("entry_threshold", 50),
            'tp': tp,
            'tp_conf': tp_conf,
            'bearish_rsi_div': bearish_rsi_div,
            'stoch_bear_crossover': stoch_bear_crossover,
            'rejection_wick': rejection_wick,
            'price_above_vwap': price_above_vwap,

        }

    except Exception as e:
        logging.error(f"Analysis error {symbol} {interval}: {e}")
        return None

# === Bot Loop ===
async def scan_symbols():
    
    intervals = {
    "30m": TIMEFRAME_CONFIG["30m"]["cooldown"],
    "4h": TIMEFRAME_CONFIG["4h"]["cooldown"],
    "1d": TIMEFRAME_CONFIG["1d"]["cooldown"]
    }

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")


    for symbol in pairs:
        for tf, cooldown in intervals.items():  # <-- This line must be indented
            data = analyze(symbol, tf)
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
    loop_counter = 0
    fetch_24h_volumes()  # Initial fetch

    while True:
        await scan_symbols()

        loop_counter += 1
        if loop_counter >= 8:  # 8 * 30min = 4 hours
            logging.info("🔄 Refreshing 24h volume data from Binance...")
            fetch_24h_volumes()
            loop_counter = 0

        await asyncio.sleep(1800)

def run():
    port = int(os.environ['PORT'])
    app.run(host='0.0.0.0', port=port)


if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    Thread(target=run).start()
    Thread(target=lambda: asyncio.run(main_loop())).start()
