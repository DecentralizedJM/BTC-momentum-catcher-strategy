import os
import time
import json
import logging
import pandas as pd
from pybit.unified_trading import WebSocket
from dotenv import load_dotenv
from mudrex_executor import MudrexExecutor

# Load env variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
WS_SYMBOL = "BTCUSDT"     # PyBit WS symbol format
MUDREX_SYMBOL = "BTCUSDT" # Mudrex symbol formatting
KLINE_INTERVAL = "15"     # 15 minute interval for pybit
QTY_TOTAL = 0.002         # 2 x 0.001 minimum order size (TP1 partial close requires taking half)
QTY_HALF = QTY_TOTAL / 2.0
LEVERAGE = 25             # 25x leverage to cover 0.002 BTC (~$140) with ~$6 margin
MAX_DEPTH = 500.0

RSI_LEN = 14
SMA_LEN = 20

STATE_FILE = "bot_state.json"
dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

class BotState:
    def __init__(self):
        self.reset()
        self.load()

    def reset(self):
        self.alert_high = None
        self.alert_low = None
        self.sl_level = None
        self.target_1 = None
        self.pending_long = False
        self.pending_short = False
        self.in_long = False
        self.in_short = False
        self.tp1_hit = False

    def load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    self.__dict__.update(data)
            except Exception as e:
                logger.error(f"Could not load state: {e}")

    def save(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self.__dict__, f, indent=4)
        except Exception as e:
            logger.error(f"Could not save state: {e}")

# Global references
state = BotState()
state.last_save_time = 0
mudrex_executor = MudrexExecutor() if not dry_run else None
historical_klines = []
last_sync_time = 0

def calculate_indicators(klines):
    """Calculates RSI and SMA from a list of kline dictionaries using pandas natively."""
    if len(klines) < SMA_LEN:
        return None, None
        
    df = pd.DataFrame(klines)
    
    # Calculate SMA
    sma = df['close'].rolling(window=SMA_LEN).mean().iloc[-1]
    
    # Calculate RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=RSI_LEN).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_LEN).mean()
    
    avg_gain = gain.copy()
    avg_loss = loss.copy()
    for i in range(RSI_LEN, len(df)):
        avg_gain.iloc[i] = (avg_gain.iloc[i-1] * (RSI_LEN - 1) + gain.iloc[i]) / RSI_LEN
        avg_loss.iloc[i] = (avg_loss.iloc[i-1] * (RSI_LEN - 1) + loss.iloc[i]) / RSI_LEN
        
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return float(rsi.iloc[-1]), float(sma)


def sync_mudrex_position():
    """Syncs actual position size from Mudrex."""
    if dry_run:
        # In dry run, simulate based on local flags
        if state.in_long: return QTY_HALF if state.tp1_hit else QTY_TOTAL
        if state.in_short: return -QTY_HALF if state.tp1_hit else -QTY_TOTAL
        return 0.0

    global last_sync_time
    now = time.time()
    if now - last_sync_time < 10: # Throttle Mudrex REST calls to protect WebSocket thread
        if state.in_long: return QTY_HALF if state.tp1_hit else QTY_TOTAL
        if state.in_short: return -QTY_HALF if state.tp1_hit else -QTY_TOTAL
        return 0.0
        
    last_sync_time = now

    try:
        pos = mudrex_executor.get_open_position(MUDREX_SYMBOL)
        if pos:
            size = float(pos.position_size)
            if pos.side == "SHORT": size = -size
            
            state.in_long = size > 0
            state.in_short = size < 0
            
            if abs(size) <= (QTY_HALF + 0.001) and not state.tp1_hit:
                state.tp1_hit = True
                logger.info("TP1 threshold detected via state sync.")
            return size
        else:
            state.in_long = False
            state.in_short = False
            return 0.0
    except Exception as e:
        logger.error(f"Error syncing position: {e}")
        return 0.0


def handle_kline_message(message):
    """Callback for Pybit WebSocket on every kline tick"""
    global historical_klines, state
    
    try:
        data = message.get("data", [])
        if not data: return
        
        tick = data[0]
        current_price = float(tick["close"])
        is_candle_closed = tick["confirm"]
        
        # 1. Update/Add to local kline history
        kline = {
            "timestamp": tick["start"],
            "open": float(tick["open"]),
            "high": float(tick["high"]),
            "low": float(tick["low"]),
            "close": float(tick["close"])
        }
        
        if len(historical_klines) == 0 or historical_klines[-1]["timestamp"] != kline["timestamp"]:
            historical_klines.append(kline)
        else:
            historical_klines[-1] = kline
            
        # Keep only necessary buffer
        if len(historical_klines) > 100:
            historical_klines.pop(0)

        # 2. Check Triggers on EVERY TICK (Sub-second execution)
        pos_size = sync_mudrex_position()
        curr_sma = None
        
        # We need SMA if we are trailing
        if len(historical_klines) >= SMA_LEN:
             _, curr_sma = calculate_indicators(historical_klines)
             
        # SEARCH FOR SETUPS (Pending Orders)
        if pos_size == 0.0:
            if state.pending_long and current_price >= state.alert_high:
                logger.info(f"🚀 EXECUTING LONG AT {current_price}")
                if not dry_run:
                    mudrex_executor.place_market_order(
                        MUDREX_SYMBOL, "LONG", QTY_TOTAL, LEVERAGE, 
                        stoploss=state.sl_level, takeprofit=state.target_1
                    )
                state.in_long = True
                state.pending_long = False
                state.save()
                
            elif state.pending_short and current_price <= state.alert_low:
                logger.info(f"🚀 EXECUTING SHORT AT {current_price}")
                if not dry_run:
                    mudrex_executor.place_market_order(
                        MUDREX_SYMBOL, "SHORT", QTY_TOTAL, LEVERAGE, 
                        stoploss=state.sl_level, takeprofit=state.target_1
                    )
                state.in_short = True
                state.pending_short = False
                state.save()

        # MANAGE OPEN LONG
        elif state.in_long:
            state.pending_long = state.pending_short = False
            
            if dry_run:
                if current_price >= state.target_1 and not state.tp1_hit:
                    logger.info("🎯 Simulated TP1 Hit!")
                    state.tp1_hit = True
                elif current_price <= state.sl_level and not state.tp1_hit:
                    logger.info("🛑 Simulated SL Hit! Resetting bot.")
                    state.reset()
            
            # Trailing SL Phase
            if state.tp1_hit and curr_sma:
                if current_price <= curr_sma:
                    logger.info("🚨 Trailing SMA touched for LONG position. Exiting full remaining balance.")
                    if not dry_run:
                        pos = mudrex_executor.get_open_position(MUDREX_SYMBOL)
                        if pos: mudrex_executor.close_full_position(pos.position_id)
                    state.reset()
                    
        # MANAGE OPEN SHORT
        elif state.in_short:
            state.pending_long = state.pending_short = False
            
            if dry_run:
                if current_price <= state.target_1 and not state.tp1_hit:
                    logger.info("🎯 Simulated TP1 Hit!")
                    state.tp1_hit = True
                elif current_price >= state.sl_level and not state.tp1_hit:
                    logger.info("🛑 Simulated SL Hit! Resetting bot.")
                    state.reset()
                    
            if state.tp1_hit and curr_sma:
                if current_price >= curr_sma:
                    logger.info("🚨 Trailing SMA touched for SHORT position. Exiting full remaining balance.")
                    if not dry_run:
                        pos = mudrex_executor.get_open_position(MUDREX_SYMBOL)
                        if pos: mudrex_executor.close_full_position(pos.position_id)
                    state.reset()

        # 3. Handle End of Candle Logic to Assess NEW Alerts
        if is_candle_closed and pos_size == 0.0 and len(historical_klines) >= RSI_LEN + 1:
            # We need previous candle for RSI trigger comparison
            curr_rsi, _ = calculate_indicators(historical_klines)
            prev_rsi, _ = calculate_indicators(historical_klines[:-1])
            
            candle_depth = kline['high'] - kline['low']
            is_valid_depth = candle_depth <= MAX_DEPTH
            c_close, c_open = kline['close'], kline['open']
            
            logger.info(f"Candle Closed | Price: {c_close} | RSI: {curr_rsi:.2f}")

            # Check Alert Conditions
            if c_close > c_open and curr_rsi > 60 and prev_rsi <= 60 and is_valid_depth:
                state.alert_high = kline['high']
                state.alert_low = kline['low']
                risk = state.alert_high - state.alert_low
                state.target_1 = state.alert_high + risk
                state.sl_level = state.alert_low
                state.pending_long = True
                state.pending_short = False
                logger.info(f"🟢 LONG ALERT setup pending. Entry High: {state.alert_high}, SL: {state.sl_level}")
                
            elif c_close < c_open and curr_rsi < 40 and prev_rsi >= 40 and is_valid_depth:
                state.alert_high = kline['high']
                state.alert_low = kline['low']
                risk = state.alert_high - state.alert_low
                state.target_1 = state.alert_low - risk
                state.sl_level = state.alert_high
                state.pending_short = True
                state.pending_long = False
                logger.info(f"🔴 SHORT ALERT setup pending. Entry Low: {state.alert_low}, SL: {state.sl_level}")

        # Regular state saves and trailing updates (every 10 seconds to not spam APIs)
        if time.time() - state.last_save_time >= 10:
            state.last_save_time = time.time()
            state.save()
            if not dry_run and state.tp1_hit and curr_sma:
                pos = mudrex_executor.get_open_position(MUDREX_SYMBOL)
                if pos:
                    # Update trailing SL explicitly on Mudrex if favorable
                    current_sl = float(pos.stoploss_price) if pos.stoploss_price else 0
                    if state.in_long and curr_sma > current_sl:
                        mudrex_executor.update_trailing_stoploss(pos.position_id, curr_sma)
                    elif state.in_short and (curr_sma < current_sl or current_sl == 0):
                        mudrex_executor.update_trailing_stoploss(pos.position_id, curr_sma)
                        
    except Exception as e:
        logger.error(f"Error in websocket loop: {e}")


def main():
    logger.info("Starting BTC Momentum Catcher Bot (WebSockets)")
    if dry_run:
        logger.info("Running in DRY RUN mode. No real orders will be placed.")
    else:
        logger.info("Running in LIVE mode. Orders WILL be executed on Mudrex.")

    # Populate initial historical klines utilizing Pybit REST so we don't have to wait 15m * 20 candles
    from pybit.unified_trading import HTTP
    session = HTTP(testnet=False)
    try:
        res = session.get_kline(category="linear", symbol=WS_SYMBOL, interval=KLINE_INTERVAL, limit=100)
        for tick in reversed(res["result"]["list"]): # API returns newest first
            historical_klines.append({
                "timestamp": int(tick[0]),
                "open": float(tick[1]),
                "high": float(tick[2]),
                "low": float(tick[3]),
                "close": float(tick[4])
            })
        logger.info(f"Loaded {len(historical_klines)} historical candles.")
    except Exception as e:
        logger.error(f"Failed to bootstrap historical data: {e}. Exiting.")
        return

    # Start PyBit WebSocket
    ws = WebSocket(
        testnet=False,
        channel_type="linear",
    )
    
    logger.info("Connecting to Bybit KLINE WebSockets...")
    ws.kline_stream(interval=KLINE_INTERVAL, symbol=WS_SYMBOL, callback=handle_kline_message)
    
    while True:
        # Keep main thread alive
        time.sleep(1)

if __name__ == "__main__":
    main()
