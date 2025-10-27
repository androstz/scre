import json
import requests
from datetime import datetime

# --- Helper Functions ---
def calculate_ema(prices, period):
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema = [0] * len(prices)
    # Seed first EMA
    ema[period - 1] = sum(prices[:period]) / period
    for i in range(period, len(prices)):
        ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
    return ema

def calculate_rsi(prices, period=14):
    if len(prices) <= period:
        return []
    changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi = []
    for i in range(len(changes)):
        if i < period:
            rs = avg_gain / avg_loss if avg_loss != 0 else 0
        else:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            rs = avg_gain / avg_loss if avg_loss != 0 else 0
        rsi_val = 100 - (100 / (1 + rs)) if rs != 0 else 100
        rsi.append(rsi_val)
    return rsi

def get_binance_symbols():
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    return [item['symbol'] for item in data if item['symbol'].endswith('USDT')]

def get_bybit_symbols():
    url = "https://api.bybit.com/derivatives/v3/public/instruments-info?category=linear"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    if data['retCode'] != 0:
        raise Exception("Bybit API error")
    return [
        item['symbol']
        for item in data['result']['list']
        if item['status'] == 'Trading' and item['quoteCoin'] == 'USDT'
    ]

def fetch_klines(exchange, symbol, interval, limit=100):
    if exchange == 'binance':
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
        resp = requests.get(url, timeout=6)
        resp.raise_for_status()
        klines = resp.json()
        closes = [float(k[4]) for k in klines]
        return closes
    elif exchange == 'bybit':
        bybit_interval_map = {'1m':'1','5m':'5','15m':'15','30m':'30','1h':'60','4h':'240','1d':'D'}
        interval = bybit_interval_map.get(interval, interval)
        url = f"https://api.bybit.com/derivatives/v3/public/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
        resp = requests.get(url, timeout=6)
        resp.raise_for_status()
        data = resp.json()
        if data['retCode'] != 0:
            return []
        closes = [float(k[4]) for k in data['result']['list']]
        return closes
    return []

# --- Main Handler ---
def handler(event, context):
    try:
        query = event.get('queryStringParameters') or {}
        exchange = query.get('exchange', 'binance')
        timeframe = query.get('timeframe', '1m')
        ema_short = int(query.get('emaShort', 9))
        ema_long = int(query.get('emaLong', 26))
        rsi_period = int(query.get('rsiPeriod', 14))

        # Validate
        if ema_short >= ema_long:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'EMA Short must be < EMA Long'})
            }

        # Get symbols
        symbols = get_binance_symbols() if exchange == 'binance' else get_bybit_symbols()
        signals = []

        for symbol in symbols[:50]:  # Limit to 50 for speed
            try:
                closes = fetch_klines(exchange, symbol, timeframe, limit=100)
                if len(closes) < max(ema_long, rsi_period) + 10:
                    continue

                ema_s = calculate_ema(closes, ema_short)
                ema_l = calculate_ema(closes, ema_long)
                rsi_vals = calculate_rsi(closes, rsi_period)

                if len(ema_s) < 2 or len(ema_l) < 2:
                    continue

                # Check fresh crossover: current short > long, previous short <= long
                if ema_s[-1] > ema_l[-1] and ema_s[-2] <= ema_l[-2]:
                    signals.append({
                        'symbol': symbol,
                        'price': closes[-1],
                        'emaShort': round(ema_s[-1], 4),
                        'emaLong': round(ema_l[-1], 4),
                        'rsi': round(rsi_vals[-1], 2) if rsi_vals else None,
                        'timeframe': timeframe
                    })
            except Exception as e:
                continue  # Skip failed symbols

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'signals': signals,
                'count': len(signals),
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            })
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }
