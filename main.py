import os
import logging
from datetime import datetime
import pytz
import requests
import pandas as pd
from flask import Flask, request
import asyncio
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands, AverageTrueRange
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
last_alert_price = {}

market_caps = {
    "BTCUSDT": "Blue Chip", "ETHUSDT": "Blue Chip", "BNBUSDT": "Blue Chip",
    "SOLUSDT": "Blue Chip", "ADAUSDT": "Blue Chip", "XRPUSDT": "Blue Chip",
    "AVAXUSDT": "Mid Cap", "DOTUSDT": "Mid Cap", "MATICUSDT": "Mid Cap",
    "NEARUSDT": "Mid Cap", "TRXUSDT": "Mid Cap", "ATOMUSDT": "Mid Cap",
    "LTCUSDT": "Mid Cap", "LINKUSDT": "Mid Cap", "BCHUSDT": "Mid Cap",
    "EGLDUSDT": "Mid Cap", "XLMUSDT": "Mid Cap", "FILUSDT": "Mid Cap",
    "APTUSDT": "Mid Cap", "OPUSDT": "Mid Cap", "ARBUSDT": "Mid Cap",
    # Low caps (partial list)
    "INJUSDT": "Low Cap", "FETUSDT": "Low Cap", "RNDRUSDT": "Low Cap",
    "ARUSDT": "Low Cap", "GRTUSDT": "Low Cap", "LDOUSDT": "Low Cap",
    "STXUSDT": "Low Cap", "CVCUSDT": "Low Cap", "CTSIUSDT": "Low Cap",
    "BANDUSDT": "Low Cap", "CFXUSDT": "Low Cap", "ZILUSDT": "Low Cap",
    "SKLUSDT": "Low Cap", "KAVAUSDT": "Low Cap", "ANKRUSDT": "Low Cap",
    "ENSUSDT": "Low Cap", "FLUXUSDT": "Low Cap", "SFPUSDT": "Low Cap",
    "ILVUSDT": "Low Cap", "AGIXUSDT": "Low Cap", "OCEANUSDT": "Low Cap",
    "DYDXUSDT": "Low Cap", "MKRUSDT": "Low Cap", "COTIUSDT": "Low Cap",
    "REQUSDT": "Low Cap", "PENDLEUSDT": "Low Cap", "ACHUSDT": "Low Cap",
    "LOOMUSDT": "Low Cap", "LINAUSDT": "Low Cap", "NMRUSDT": "Low Cap",
    "IDUSDT": "Low Cap", "DOGEUSDT": "Mid Cap"
}

def get_category(symbol):
    return market_caps.get(symbol, "Uncategorized")

def get_time():
    tz = pytz.timezone("Asia/Kolkata")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

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
    suggestion = interpret_confidence(data['confidence'])
    category = get_category(data['symbol'])
    return f"""
🟢 *[ENTRY]* — {data['symbol']} ({data['interval']}) [{category}]
*Confidence:* {suggestion}
RSI: {data['rsi']} | Stoch %K: {data['stoch_k']} / %D: {data['stoch_d']}
Price at Lower BB ✅ | Volume Spike {'✅' if data['volume_spike'] else '❌'} | Trend: {'Bullish ✅' if data['trend'] else '❌'}
Suppression: {'Yes ❌' if data['suppressed'] else 'No ✅'} | RSI Divergence: {'Yes ✅' if data['divergence'] else 'No ❌'}
Initial SL: {data['initial_sl']}
TP Target: {data['bb_upper']} | TSL Level: {data['tsl_level']} ({round((1 - data['tsl_level']/data['highest']) * 100, 2)}%)
Price: {data['price']} | Time: {get_time()}
"""

def should_alert(symbol, interval, price):
    key = f"{symbol}_{interval}"
    last_price = last_alert_price.get(key, None)
    if last_price is None or abs(price - last_price) > price * 0.01:
        last_alert_price[key] = price
        return True
    return False

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

# NOTE: continue with analyze(), tp_msg(), tsl_msg(), main_loop() as in your current working version.
# Let me know if you'd like those included as well.
