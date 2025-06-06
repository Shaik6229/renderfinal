
import asyncio
import requests
from datetime import datetime
from telegram import Bot
from flask import Flask
from threading import Thread

# --- Flask app to keep server alive ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- Telegram Setup ---
TELEGRAM_TOKEN = "8196216430:AAHSrVGKnQJgO61qjGDhMSHeK1xddrMje4g"
USER_ID = 7128406135
bot = Bot(token=TELEGRAM_TOKEN)

# You can add your analysis and alert logic here...
print("Bot initialized and ready.")
