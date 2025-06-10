import logging
import pytz
import requests
import pandas as pd
from datetime import datetime
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator

def fetch_ohlcv(symbol, interval, limit=500):
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}'
    try:
        data = requests.get(url).json()
        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'trades',
            'taker_buy_base', 'taker_buy_quote', 'ignore'
        ])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        return df
    except Exception as e:
        logging.error(f"Error fetching OHLCV: {e}")
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

def volume_spike(df):
    try:
        vol = df['volume'].iloc[-20:]
        return df['volume'].iloc[-1] > vol.mean() + 1.5 * vol.std()
    except Exception as e:
        logging.error(f"Volume spike error: {e}")
        return False

def rsi_divergence(df):
    try:
        rsi_vals = RSIIndicator(df['close']).rsi().iloc[-15:]
        lows_price = df['low'].iloc[-15:]
        price_lows_idx = lows_price.nsmallest(2).index.tolist()
        if len(price_lows_idx) < 2:
            return False
        first, second = price_lows_idx
        return lows_price[first] > lows_price[second] and rsi_vals[first] < rsi_vals[second]
    except Exception as e:
        logging.error(f"RSI divergence error: {e}")
        return False

def check_trend(symbol, interval):
    df = fetch_ohlcv(symbol, interval)
    if df.empty or len(df) < 200:
        return False
    ema_200 = EMAIndicator(df['close'], 200).ema_indicator().iloc[-1]
    return df['close'].iloc[-1] > ema_200

def get_time():
    return datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")

def categorize_by_mcap(symbol):
    symbol_to_id = {
        "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "BNBUSDT": "binancecoin",
        "SOLUSDT": "solana", "ADAUSDT": "cardano", "XRPUSDT": "ripple",
        "AVAXUSDT": "avalanche-2", "DOTUSDT": "polkadot", "MATICUSDT": "matic-network",
        "NEARUSDT": "near", "TRXUSDT": "tron", "ATOMUSDT": "cosmos",
        "LTCUSDT": "litecoin", "LINKUSDT": "chainlink", "BCHUSDT": "bitcoin-cash",
        "EGLDUSDT": "multiversx", "XLMUSDT": "stellar", "FILUSDT": "filecoin",
        "APTUSDT": "aptos", "OPUSDT": "optimism", "ARBUSDT": "arbitrum",
        "INJUSDT": "injective", "SFPUSDT": "safe-palace", "CVCUSDT": "civic",
        "CTSIUSDT": "cartesi", "BANDUSDT": "band-protocol", "CFXUSDT": "conflux",
        "ZILUSDT": "zilliqa", "SKLUSDT": "skale", "KAVAUSDT": "kava",
        "ANKRUSDT": "ankr-network", "ENSUSDT": "ens", "FLUXUSDT": "flux",
        "ILVUSDT": "ilv", "AGIXUSDT": "singularitynet", "OCEANUSDT": "ocean-protocol",
        "DYDXUSDT": "dydx", "MKRUSDT": "maker", "COTIUSDT": "coti",
        "REQUSDT": "request", "PENDLEUSDT": "pendle", "ACHUSDT": "alchemy-pay",
        "LOOMUSDT": "loom-network", "LINAUSDT": "linear", "NMRUSDT": "numeraire",
        "IDUSDT": "idu", "ARUSDT": "arweave"
    }

    coingecko_id = symbol_to_id.get(symbol)
    if not coingecko_id:
        return "Low Cap 🧪"

    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {"vs_currency": "usd", "ids": coingecko_id}
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status()
        data = res.json()
        if not data:
            return "Low Cap 🧪"

        market_cap = data[0].get("market_cap", 0)
        if market_cap >= 10_000_000_000:
            return "Blue Chip 💎"
        elif market_cap >= 1_000_000_000:
            return "Mid Cap ⚙️"
        else:
            return "Low Cap 🧪"
    except Exception as e:
        logging.error(f"Market cap fetch error for {symbol}: {e}")
        return "Low Cap 🧪"
