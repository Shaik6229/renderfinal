from alerts import send_alert_if_needed
from flask import Flask
from threading import Thread
import asyncio
import nest_asyncio

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

if __name__ == '__main__':
    nest_asyncio.apply()
    Thread(target=run_flask).start()
    asyncio.run(send_alert_if_needed())
