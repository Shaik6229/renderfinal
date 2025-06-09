import os
import logging
import requests
from flask import Flask, request

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
        return "❌ Missing environment variables!", 500
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        resp = requests.post(url, data=data)
        return "Test alert sent!" if resp.status_code == 200 else f"Failed: {resp.text}", resp.status_code
    except Exception as e:
        logging.error(f"Error sending test alert: {e}")
        return f"Error: {e}", 500

def run():
    app.run(host='0.0.0.0', port=8080)
