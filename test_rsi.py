import ccxt
import pandas as pd
import numpy as np

def calculate_tv_rsi(df, length=14):
    """
    Calculates RSI exactly the same way TradingView does.
    TradingView uses the RMA (Moving Average used in RSI) rather than EMA or SMA.
    RMA: alpha = 1 / length
    """
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    
    # TradingView uses an Exponential Moving Average with alpha = 1/length (RMA)
    # The first value is a simple moving average of the first `length` periods.
    # To get exact TV values we need a decent lookback period (e.g. 100+ candles).
    
    avg_gain = gain.ewm(alpha=1/length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, min_periods=length, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def test_data_fetch():
    print("Testing Bybit CCXT connection & True TV RSI...")
    exchange = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    symbol = "BTC/USDT:USDT"
    
    ohlcv = exchange.fetch_ohlcv(symbol, "15m", limit=300) # Give it 300 to smooth EMA perfectly
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    df['tv_rsi'] = calculate_tv_rsi(df, 14)
    last_row = df.iloc[-1]
    
    print(f"Data Fetch successful! Last Close: {last_row['close']}, RSI: {last_row['tv_rsi']:.2f}")

if __name__ == "__main__":
    test_data_fetch()
