import ccxt
import pandas as pd
import pandas as pd

def test_data_fetch():
    print("Testing Bybit CCXT connection...")
    exchange = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    
    symbol = "BTC/USDT:USDT"
    print(f"Fetching {symbol} 15m klines...")
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, "15m", limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        # Calculate SMA
        df['sma'] = df['close'].rolling(window=20).mean()
        
        # Calculate RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        
        avg_gain = gain.copy()
        avg_loss = loss.copy()
        for i in range(14, len(df)):
            avg_gain.iloc[i] = (avg_gain.iloc[i-1] * 13 + gain.iloc[i]) / 14
            avg_loss.iloc[i] = (avg_loss.iloc[i-1] * 13 + loss.iloc[i]) / 14
            
        rs = avg_gain / avg_loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        last_row = df.iloc[-1]
        print(f"Data Fetch successful! Last Close: {last_row['close']}, RSI: {last_row['rsi']:.2f}, SMA: {last_row['sma']:.2f}")
        return True
    except Exception as e:
        print(f"Failed to fetch data: {e}")
        return False

if __name__ == "__main__":
    test_data_fetch()
