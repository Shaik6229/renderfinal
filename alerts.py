import requests
import logging
from utils import get_time

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
    return f"""
🟢 *[ENTRY]* — {data['symbol']} ({data['interval']})
*Confidence:* {suggestion}
RSI: {data['rsi']} | Stoch %K: {data['stoch_k']} / %D: {data['stoch_d']}
Price at Lower BB ✅ | Volume Spike {'✅' if data['volume_spike'] else '❌'} | Trend: {'Bullish ✅' if data['trend'] else '❌'}
Suppression: {'Yes ❌' if data['suppressed'] else 'No ✅'} | RSI Divergence: {'Yes ✅' if data['divergence'] else 'No ❌'}
Initial SL: {data['initial_sl']}
TP Target: {data['bb_upper']} | TSL Level: {data['tsl_level']} ({round((1 - data['tsl_level']/data['highest']) * 100, 2)}%)
Price: {data['price']} | Time: {get_time()}
"""

def tp_msg(data):
    confidence = 0
    confidence += 25 if data['rsi'] > 70 else 0
    confidence += 25 if data['stoch_k'] > 80 and data['stoch_d'] > 80 else 0
    confidence += 25 if data['price'] >= data['bb_upper'] else 0
    confidence += 25 if not data['suppressed'] else 0
    suggestion = interpret_confidence(min(confidence, 100))
    return f"""
🟡 *[TAKE PROFIT]* — {data['symbol']} ({data['interval']})
*Confidence:* {suggestion}
Price near Upper BB ✅ | RSI: {data['rsi']} | Stoch %K: {data['stoch_k']} / %D: {data['stoch_d']}
Price: {data['price']} | Time: {get_time()}
"""

def tsl_msg(data):
    return f"""
🔴 [TRAILING STOP HIT] — {data['symbol']} ({data['interval']})
Price: {data['price']} fell below TSL level: {data['tsl_level']}
Time: {get_time()}
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
