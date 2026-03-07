import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

def fetch_data():
    exchange = ccxt.binance()
    symbol = 'BTC/USDT'
    timeframe = '15m'
    
    # 3 years ago from today
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=3*365)
    
    since = exchange.parse8601(start_date.isoformat() + 'Z')
    end = exchange.parse8601(end_date.isoformat() + 'Z')
    all_klines = []
    
    print(f"Fetching data from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...")
    
    while since < end:
        try:
            klines = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not len(klines):
                break
            
            all_klines.extend(klines)
            since = klines[-1][0] + 15 * 60 * 1000 # Next 15m candle
            time.sleep(0.05) # Rate limit protection
        except Exception as e:
            print(f"Error fetching: {e}")
            time.sleep(2)
            
    df = pd.DataFrame(all_klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    # Remove duplicates
    df = df.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
    return df

def run_backtest(df):
    RSI_LEN = 14
    SMA_LEN = 20
    MAX_DEPTH = 500.0
    
    # Setup Indicators safely
    df['sma'] = df['close'].rolling(window=SMA_LEN).mean()
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/RSI_LEN, min_periods=RSI_LEN, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/RSI_LEN, min_periods=RSI_LEN, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    df['sma200'] = df['close'].rolling(window=200).mean()
    df['vol_ma'] = df['volume'].rolling(window=20).mean()

    QTY_TOTAL = 0.02
    QTY_HALF = QTY_TOTAL / 2.0
    FEE_RATE = 0.0005 # 0.05% Flat Fee
    
    trades = []
    
    # State tracking
    pending_long = False
    pending_short = False
    alert_high = None
    alert_low = None
    sl_level = None
    target_1 = None
    
    in_long = False
    in_short = False
    tp1_hit = False
    entry_price = 0.0
    
    for i in range(200, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]
        
        high = row['high']
        low = row['low']
        close = row['close']
        open_c = row['open']
        sma20 = prev_row['sma'] # Use prev SMA since current SMA is closing value
        
        # 1. Check existing trades
        if in_long:
            if not tp1_hit:
                if low <= sl_level:
                    # SL Hit
                    exit_price = sl_level
                    pnl = (exit_price - entry_price) * QTY_TOTAL
                    fees = (exit_price * QTY_TOTAL * FEE_RATE) + (entry_price * QTY_TOTAL * FEE_RATE)
                    trades.append({"type": "SL", "pnl": pnl - fees, "win": False})
                    in_long = False
                elif high >= target_1:
                    # TP1 Hit
                    tp1_hit = True
                    tp1_pnl = (target_1 - entry_price) * QTY_HALF
                    tp1_fees = (target_1 * QTY_HALF * FEE_RATE) + (entry_price * QTY_HALF * FEE_RATE)
                    trades.append({"type": "TP1", "pnl": tp1_pnl - tp1_fees, "win": True})
            
            if tp1_hit and in_long:
                if low <= sma20:
                    # Trailing SL Hit
                    exit_price = sma20
                    pnl = (exit_price - entry_price) * QTY_HALF
                    fees = (exit_price * QTY_HALF * FEE_RATE)
                    trades.append({"type": "TRAIL", "pnl": pnl - fees, "win": pnl > 0})
                    in_long = False
                    tp1_hit = False
                    
        elif in_short:
            if not tp1_hit:
                if high >= sl_level:
                    exit_price = sl_level
                    pnl = (entry_price - exit_price) * QTY_TOTAL
                    fees = (exit_price * QTY_TOTAL * FEE_RATE) + (entry_price * QTY_TOTAL * FEE_RATE)
                    trades.append({"type": "SL", "pnl": pnl - fees, "win": False})
                    in_short = False
                elif low <= target_1:
                    tp1_hit = True
                    tp1_pnl = (entry_price - target_1) * QTY_HALF
                    tp1_fees = (target_1 * QTY_HALF * FEE_RATE) + (entry_price * QTY_HALF * FEE_RATE)
                    trades.append({"type": "TP1", "pnl": tp1_pnl - tp1_fees, "win": True})
            
            if tp1_hit and in_short:
                if high >= sma20:
                    exit_price = sma20
                    pnl = (entry_price - exit_price) * QTY_HALF
                    fees = (exit_price * QTY_HALF * FEE_RATE)
                    trades.append({"type": "TRAIL", "pnl": pnl - fees, "win": pnl > 0})
                    in_short = False
                    tp1_hit = False

        # 2. Check Pending Executions
        if not in_long and not in_short:
            if pending_long and high >= alert_high:
                in_long = True
                entry_price = alert_high
                pending_long = False
                tp1_hit = False
            elif pending_short and low <= alert_low:
                in_short = True
                entry_price = alert_low
                pending_short = False
                tp1_hit = False

        # 3. Assess New Setups
        # To strictly replicate PineScript we only look for setups if NOT in a trade
        if not in_long and not in_short:
            curr_rsi = row['rsi']
            prev_rsi = prev_row['rsi']
            sma200 = prev_row['sma200']
            
            candle_depth = high - low
            is_valid_depth = candle_depth <= MAX_DEPTH
            is_valid_volume = row['volume'] > (prev_row['vol_ma'] * 1.5)
            
            long_rsi_cross = curr_rsi > 60 and prev_rsi <= 60
            short_rsi_cross = curr_rsi < 40 and prev_rsi >= 40
            
            # Trend filter
            is_uptrend = close > sma200
            is_downtrend = close < sma200
            
            if close > open_c and long_rsi_cross and is_valid_depth and is_valid_volume and is_uptrend:
                alert_high = high
                alert_low = low
                risk = alert_high - alert_low
                target_1 = alert_high + risk
                sl_level = alert_low
                pending_long = True
                pending_short = False
            elif close < open_c and short_rsi_cross and is_valid_depth and is_valid_volume and is_downtrend:
                alert_high = high
                alert_low = low
                risk = alert_high - alert_low
                target_1 = alert_low - risk
                sl_level = alert_high
                pending_short = True
                pending_long = False

    return trades

if __name__ == "__main__":
    df = fetch_data()
    print(f"Total candles loaded: {len(df)}")
    
    trades = run_backtest(df)
    print(f"Total entries triggered: {len([t for t in trades if t['type'] in ['SL', 'TP1']])}")
    
    # Combining TP1 + Trail into full trades
    full_trades = []
    current_trade_pnl = 0
    in_trade = False
    
    for t in trades:
        if t['type'] == 'SL':
            full_trades.append({"pnl": t['pnl'], "win": t['win']})
        elif t['type'] == 'TP1':
            current_trade_pnl = t['pnl']
            in_trade = True
        elif t['type'] == 'TRAIL' and in_trade:
            current_trade_pnl += t['pnl']
            full_trades.append({"pnl": current_trade_pnl, "win": current_trade_pnl > 0})
            in_trade = False
            
    num_trades = len(full_trades)
    winning_trades = len([t for t in full_trades if t['win']])
    
    if num_trades > 0:
        win_rate = (winning_trades / num_trades) * 100
        total_pnl = sum([t['pnl'] for t in full_trades])
        print(f"Total Trades: {num_trades}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Total Net PNL: ${total_pnl:.2f}")
    else:
        print("No trades found.")
