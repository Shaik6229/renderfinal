import os
import logging
from datetime import datetime
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
import pytz
import random

# Setup logging
logging.basicConfig(filename='log.txt', level=logging.INFO, format='%(asctime)s %(message)s')
logging.info("Bot initialized and ready.")

# Flask app
app = Flask(__name__)

# Telegram bot
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
bot = Bot(token=BOT_TOKEN)

# Define IST timezone using pytz
IST = pytz.timezone('Asia/Kolkata')

# Dummy function to simulate indicator values
def get_fake_indicators():
    return {
        "rsi": round(random.uniform(15, 25), 1),
        "stoch_9": round(random.uniform(10, 20), 1),
        "stoch_14": round(random.uniform(15, 25), 1),
        "stoch_40": round(random.uniform(20, 30), 1),
        "stoch_60": round(random.uniform(18, 28), 1),
        "current_price": round(random.uniform(145, 147), 2),
        "buy_price": round(random.uniform(145.5, 146.9), 2),
        "stop_loss": round(random.uniform(143, 145), 2),
        "target_low": round(random.uniform(151.0, 151.5), 2),
        "target_high": round(random.uniform(153.0, 154.5), 2)
    }

# Time formatter using pytz-aware datetime
def get_time():
    return datetime.now(IST).strftime("%Y-%m-%d %I:%M:%S %p IST")

# Buy alert message
def generate_entry_alert(symbol="SOLUSDT", timeframe="15m"):
    ind = get_fake_indicators()
    return f"""
🟢 [ENTRY ALERT] — {symbol} ({timeframe})
RSI: {ind['rsi']}
Stoch(9): {ind['stoch_9']} | Stoch(14): {ind['stoch_14']}
Stoch(40): {ind['stoch_40']} | Stoch(60): {ind['stoch_60']}
Touching lower Bollinger Band 📉
Divergence: ✅ Bullish divergence spotted

Current Price: {ind['current_price']} USDT
Suggested Limit Buy: {ind['buy_price']} USDT
Stop Loss: {ind['stop_loss']} USDT
Target Range: {ind['target_low']} – {ind['target_high']} USDT

Trend: Bullish ✅
Volume Spike: Confirmed 🔥

🗓 Timeframe: {timeframe}
⏰ Time: {get_time()}"""

# TP alert message
def generate_tp_alert(symbol="SOLUSDT", timeframe="15m"):
    ind = get_fake_indicators()
    return f"""
🔵 [TAKE PROFIT SIGNAL] — {symbol} ({timeframe})
🔹 Price is near upper Bollinger Band
🔻 Multiple stochastics are overbought
♻ Reversal signs forming

📌 Current Price: {ind['current_price']} USDT
🎯 Suggested Exit Zone: {ind['target_low']} – {ind['target_high']} USDT

🗓 Timeframe: {timeframe}
⏰ Time: {get_time()}"""

# Strategy logic
def run_strategy():
    logging.info("Running strategy scan...")
    timeframes = ["15m", "1h", "1d"]
    for tf in timeframes:
        entry_msg = generate_entry_alert(timeframe=tf)
        bot.send_message(chat_id=CHAT_ID, text=entry_msg)
        logging.info(f"Sent entry alert for {tf}")
        
        # Only TP alert for 1h and 1d
        if tf in ["1h", "1d"]:
            tp_msg = generate_tp_alert(timeframe=tf)
            bot.send_message(chat_id=CHAT_ID, text=tp_msg)
            logging.info(f"Sent TP alert for {tf}")

# Send test alert on start
try:
    test_msg = generate_entry_alert()
    bot.send_message(chat_id=CHAT_ID, text="[TEST ALERT ON STARTUP]\n" + test_msg)
    logging.info("Test alert sent successfully.")
except Exception as e:
    logging.error(f"Error sending test alert: {e}")

# Start scheduler with pytz timezone
scheduler = BackgroundScheduler(timezone=IST)
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
