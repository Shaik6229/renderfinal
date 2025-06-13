import os
import logging
from datetime import datetime, timedelta
import pytz
import requests
import pandas as pd
from flask import Flask, request
import asyncio
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.trend import EMAIndicator, MACD

# --- Toggle Debug Logging ---
DEBUG = False  # Set to True for full debug logs
log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(format=' %(levelname)s - %(message)s', level=log_level)


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
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
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
    low_cap_symbols = ['CVCUSDT', 'CTSIUSDT', 'BANDUSDT', 'KAVAUSDT', 'FLUXUSDT', 'SFPUSDT', 'ILVUSDT', 'AGIXUSDT']

    multiplier = 1.2 if symbol in low_cap_symbols else 1.5
    return current_vol > mean_vol + multiplier * std_vol

def rsi_divergence(df, lookback=20):
    try:
        rsi = RSIIndicator(df['close']).rsi()
        lows = df['low']
        closes = df['close']

        if len(df) < lookback:
            return False

        # Find recent swing lows in price and corresponding RSI
        recent_lows = []
        for i in range(1, lookback - 1):
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                recent_lows.append(i)

        if len(recent_lows) < 2:
            return False

        # Take the last two valid swing lows
        idx1, idx2 = recent_lows[-2], recent_lows[-1]

        price_divergence = lows.iloc[idx1] > lows.iloc[idx2]
        rsi_divergence = rsi.iloc[idx1] < rsi.iloc[idx2]

        return price_divergence and rsi_divergence

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
    signal = "✅ Strong" if data['entry_confidence'] >= 85 else "⚠️ Moderate" if data['entry_confidence'] >= 70 else "❌ Low"
    
    htf_status = f"15m: {'✅' if data['trend_15m'] else '❌'} | 1H: {'✅' if data['htf_1h'] else '❌'} | 1D: {'✅' if data['htf_1d'] else '❌'}"

    return f"""🟢 ENTRY — {data['symbol']} | {data['interval']} ({category})
Confidence: {data['entry_confidence']}% {signal}
→ RSI: {data['rsi']} | Stoch: {data['stoch_k']} / {data['stoch_d']}
→ BB Lower {"✅" if data['price'] <= data['bb_lower'] else "❌"} | Vol Spike: {"✅" if data['volume_spike'] else "❌"} | MACD: {"✅" if data['macd_trending_up'] else "❌"}
Trend Confluence: {htf_status}
Price: {data['price']} | ATR Strong: {"✅" if data['atr_strong'] else "❌"}
🕒 {get_time()}"""

def tp_msg(data):
    category = categorize_by_mcap(data['symbol'])
    signal = "✅ Strong" if data['tp_confidence'] >= 85 else "⚠️ Moderate" if data['tp_confidence'] >= 70 else "❌ Low"
    return f"""🟡 TP — {data['symbol']} | {data['interval']} ({category})
Confidence: {data['tp_confidence']}% {signal}
→ RSI: {data['rsi']} | Stoch: {data['stoch_k']} / {data['stoch_d']}
→ BB Upper {"✅" if data['price'] >= data['bb_upper'] else "❌"} | MACD: {"✅" if data['macd_trending_up'] else "❌"} | Suppression: {"No ✅" if not data['suppressed'] else "Yes ❌"}
Price: {data['price']} | High: {data['highest']}
🕒 {get_time()}"""    

async def send_telegram_message(bot_token, chat_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        resp = requests.post(url, data=data)
        if resp.status_code == 200:
            return resp.json()
        logging.error(f"Telegram error {resp.status_code}: {resp.text}")
    except Exception as e:
        logging.error(f"Telegram send error: {e}")

def alert_cooldown_passed(symbol, interval, kind, cooldown_minutes):
    key = f'{symbol}_{interval}_{kind}'
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
        candlestick_body = abs(price - df.iloc[-1]["open"])

        # --- HTF Trend Confluence ---
        trend_15m = check_trend(symbol, interval)
        htf_1h = check_trend(symbol, "1h") if interval == "15" else True
        htf_1d = check_trend(symbol, "1d") if interval == "15" else True
        trend = trend_15m and htf_1h and htf_1d

        suppressed = is_suppressed(df)
        vol_spike = volume_spike(df, symbol)
        divergence = rsi_divergence(df)

        macd_indicator = MACD(df['close'])
        macd = macd_indicator.macd().iloc[-1]
        macd_signal = macd_indicator.macd_signal().iloc[-1]
        macd_trending_up = macd > macd_signal and macd > 0

        atr = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range().iloc[-1]
        atr_strong = candlestick_body > atr

        highest = df['high'].max()

        # --- Entry Scoring ---
        entry_score = 0
        weights = {
            'price_below_bb': 15,
            'rsi_oversold': 15,
            'stoch_oversold': 10,
            'trend_ema200': 15,
            'not_suppressed': 10,
            'volume_spike': 15,
            'macd_trending': 10,
            'close_below_bb': 5,
            'atr_strong': 5
        }

        if price <= bb_lower:
            entry_score += weights['price_below_bb']
        if rsi < 35:
            entry_score += weights['rsi_oversold']
        if stoch_k < 30 and stoch_d < 30:
            entry_score += weights['stoch_oversold']
        if trend:
            entry_score += weights['trend_ema200']
        if not suppressed:
            entry_score += weights['not_suppressed']
        if vol_spike:
            entry_score += weights['volume_spike']
        if macd_trending_up:
            entry_score += weights['macd_trending']
        if price < bb_lower:
            entry_score += weights['close_below_bb']
        if atr_strong:
            entry_score += weights['atr_strong']

        total_score = sum(weights.values())
        entry_confidence = round((entry_score / total_score) * 100, 2)
        entry = entry_confidence >= 70

        # --- TP Logic ---
        tp = (price >= bb_upper) and (rsi > 70 or (stoch_k > 80 and stoch_d > 80))
        tp_confidence = 0
        tp_confidence += 30 if price >= bb_upper else 0
        tp_confidence += 25 if rsi > 70 else 0
        tp_confidence += 20 if stoch_k > 80 and stoch_d > 80 else 0
        tp_confidence += 15 if not suppressed else 0
        tp_confidence += 10 if macd_trending_up else 0
        tp_confidence = min(tp_confidence, 100)

        # Secondary confidence
        confidence = 0
        confidence += 20 if trend else 0
        confidence += 20 if vol_spike else 0
        confidence += 20 if not suppressed else 0
        confidence += 20 if divergence else 0
        confidence += 10 if macd_trending_up else 0
        confidence += 10 if atr_strong else 0
        confidence = min(confidence, 100)

        return {
            'symbol': symbol,
            'interval': interval,
            'entry': entry,
            'tp': tp,
            'confidence': confidence,
            'entry_confidence': entry_confidence,
            'tp_confidence': tp_confidence,
            'rsi': round(rsi, 2),
            'stoch_k': round(stoch_k, 2),
            'stoch_d': round(stoch_d, 2),
            'price': round(price, 4),
            'bb_upper': round(bb_upper, 4),
            'bb_lower': round(bb_lower, 4),
            'trend': trend,
            'trend_15m': trend_15m,
            'htf_1h': htf_1h,
            'htf_1d': htf_1d,
            'suppressed': suppressed,
            'volume_spike': vol_spike,
            'divergence': divergence,
            'macd': round(macd, 4),
            'macd_signal': round(macd_signal, 4),
            'macd_trending_up': macd_trending_up,
            'atr': round(atr, 4),
            'atr_strong': atr_strong,
            'highest': round(highest, 4)
        }

    except Exception as e:
        logging.error(f"Error analyzing {symbol} {interval}: {e}")
        if DEBUG:
            logging.debug(f"{symbol} {interval} — Data sample:\n{df.tail()}")
        return None


async def scan_symbols_for_intervals(interval_list):
    pairs = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "AVAXUSDT", "DOTUSDT", "MATICUSDT", "NEARUSDT", "ATOMUSDT",
        "LTCUSDT", "LINKUSDT", "BCHUSDT", "EGLDUSDT", "XLMUSDT",
        "FILUSDT", "APTUSDT", "OPUSDT", "ARBUSDT", "FETUSDT",
        "RNDRUSDT", "CVCUSDT", "CTSIUSDT", "BANDUSDT", "GRTUSDT",
        "STXUSDT", "AGIXUSDT", "OCEANUSDT", "DYDXUSDT", "MKRUSDT",
        "IDUSDT", "TAOUSDT"
    ]
    intervals = {"15": 10, "1h": 30, "1d": 60}
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
    counter = 0
    while True:
        await scan_symbols_for_intervals(["15"])  # Always scan 15m

        if counter % 2 == 0:
            await scan_symbols_for_intervals(["1h"])

        if counter % 4 == 0:
            await scan_symbols_for_intervals(["1d"])

        counter += 1
        await asyncio.sleep(900)  # Run every 15 minutes
from threading import Thread

def start_bot():
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main_loop())    

if __name__ == '__main__':
    Thread(target=run).start()
    Thread(target=start_bot).start()
