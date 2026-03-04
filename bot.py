import os
import time
import json
import logging
import ccxt
import pandas as pd
from dotenv import load_dotenv
from mudrex_executor import MudrexExecutor

# Load env variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
SYMBOL = "BTC/USDT:USDT"  # CCXT Bybit Perpetual formatting
MUDREX_SYMBOL = "BTCUSDT" # Mudrex symbol formatting
TIMEFRAME = "15m"
QTY_TOTAL = 0.02
QTY_HALF = QTY_TOTAL / 2.0
LEVERAGE = 10
MAX_DEPTH = 500.0

RSI_LEN = 14
SMA_LEN = 20

STATE_FILE = "bot_state.json"

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


def fetch_and_analyze(exchange):
    """Fetches OHLCV data from Bybit and calculates RSI and SMA over it."""
    try:
        # Fetch last 100 candles (padding for RSI and SMA)
        ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # Calculate SMA
        df['sma'] = df['close'].rolling(window=SMA_LEN).mean()
        
        # Calculate RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=RSI_LEN).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_LEN).mean()
        
        # Wilder's smoothing for RSI
        avg_gain = gain.copy()
        avg_loss = loss.copy()
        for i in range(RSI_LEN, len(df)):
            avg_gain.iloc[i] = (avg_gain.iloc[i-1] * (RSI_LEN - 1) + gain.iloc[i]) / RSI_LEN
            avg_loss.iloc[i] = (avg_loss.iloc[i-1] * (RSI_LEN - 1) + loss.iloc[i]) / RSI_LEN
            
        rs = avg_gain / avg_loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        return df
    except Exception as e:
        logger.error(f"Error fetching data from exchange: {e}")
        return None

def main():
    logger.info("Starting BTC Momentum Catcher Bot...")
    
    # Check if we should dry-run
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if dry_run:
        logger.info("Running in DRY RUN mode. No real orders will be placed.")
    else:
        logger.info("Running in LIVE mode. Orders WILL be executed on Mudrex.")

    state = BotState()
    
    # Initialize exchange (Bybit for market data)
    exchange = ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'} # We want perpetuals
    })
    
    # Initialize executor (Mudrex for order management) if not dry run
    mudrex_executor = MudrexExecutor() if not dry_run else None
    
    # Ensure sync loop is safe
    while True:
        try:
            # 1. Fetch data & assess current market bar
            df = fetch_and_analyze(exchange)
            if df is None or len(df) < SMA_LEN:
                time.sleep(10)
                continue
                
            # Current (incomplete) candle is df.iloc[-1], last completed is df.iloc[-2]
            # In Pine Script, conditions are confirmed "on close", so we assess based on the last completed candle.
            prev_candle = df.iloc[-2]
            prev2_candle = df.iloc[-3]
            current_candle = df.iloc[-1]
            current_price = current_candle['close']
            
            # Extract indicators
            curr_rsi = prev_candle['rsi']
            prev_rsi = prev2_candle['rsi']
            curr_sma = prev_candle['sma']
            
            candle_depth = prev_candle['high'] - prev_candle['low']
            is_valid_depth = candle_depth <= MAX_DEPTH
            
            logger.info(f"Price: {current_price:.2f} | RSI: {curr_rsi:.2f} | SMA20: {curr_sma:.2f}")

            # Check for Alert Candles (Crossover exactly over 60/40)
            long_alert_cond = (prev_candle['close'] > prev_candle['open'] and 
                               curr_rsi > 60 and prev_rsi <= 60 and 
                               is_valid_depth)
                               
            short_alert_cond = (prev_candle['close'] < prev_candle['open'] and 
                                curr_rsi < 40 and prev_rsi >= 40 and 
                                is_valid_depth)

            # Determine position state locally (or from Mudrex)
            pos_size = 0.0
            if not dry_run:
                # Sync true position sizing from Mudrex
                pos = mudrex_executor.get_open_position(MUDREX_SYMBOL)
                if pos:
                    # Mudrex position sizes are returned strictly positive; check 'side'
                    pos_size = float(pos.position_size)
                    target_qty = float(pos.position_size)
                    if pos.side == "SHORT":
                        pos_size = -pos_size

                    state.in_long = pos_size > 0
                    state.in_short = pos_size < 0
                    
                    # If position size drops from 0.02 to exactly 0.01 (or <= QTY_HALF tolerance)
                    if abs(pos_size) <= (QTY_HALF + 0.001) and not state.tp1_hit:
                        state.tp1_hit = True
                        logger.info("TP1 threshold detected via state sync.")
                        
                else:
                    state.in_long = False
                    state.in_short = False
                    pos_size = 0.0

            else:
                # In dry run, simulate position states using strictly internal tracking
                if state.in_long: pos_size = QTY_HALF if state.tp1_hit else QTY_TOTAL
                elif state.in_short: pos_size = -QTY_HALF if state.tp1_hit else -QTY_TOTAL

            # -------------------------------------------------------------
            # SEARCH FOR SETUPS (State 0)
            # -------------------------------------------------------------
            if pos_size == 0.0:
                if long_alert_cond:
                    state.alert_high = prev_candle['high']
                    state.alert_low = prev_candle['low']
                    risk = state.alert_high - state.alert_low
                    state.target_1 = state.alert_high + risk
                    state.sl_level = state.alert_low
                    state.pending_long = True
                    state.pending_short = False
                    logger.info(f"🟢 LONG ALERT setup pending. Entry High: {state.alert_high}, SL: {state.sl_level}")

                if short_alert_cond:
                    state.alert_high = prev_candle['high']
                    state.alert_low = prev_candle['low']
                    risk = state.alert_high - state.alert_low
                    state.target_1 = state.alert_low - risk
                    state.sl_level = state.alert_high
                    state.pending_short = True
                    state.pending_long = False
                    logger.info(f"🔴 SHORT ALERT setup pending. Entry Low: {state.alert_low}, SL: {state.sl_level}")

                # TRIGGER PENDING ORDERS if alert logic satisfied
                if state.pending_long and current_price >= state.alert_high:
                    logger.info(f"🚀 EXECUTING LONG AT {current_price}")
                    if not dry_run:
                        # Mudrex Market Order (The TP and SL are set alongside order)
                        mudrex_executor.place_market_order(
                            MUDREX_SYMBOL, "LONG", QTY_TOTAL, LEVERAGE, 
                            stoploss=state.sl_level, takeprofit=state.target_1
                        )
                    state.in_long = True
                    state.pending_long = False
                    
                elif state.pending_short and current_price <= state.alert_low:
                    logger.info(f"🚀 EXECUTING SHORT AT {current_price}")
                    if not dry_run:
                        mudrex_executor.place_market_order(
                            MUDREX_SYMBOL, "SHORT", QTY_TOTAL, LEVERAGE, 
                            stoploss=state.sl_level, takeprofit=state.target_1
                        )
                    state.in_short = True
                    state.pending_short = False


            # -------------------------------------------------------------
            # MANAGE OPEN LONG
            # -------------------------------------------------------------
            elif state.in_long:
                # Ensure no pending logic exists
                state.pending_long = False
                state.pending_short = False
                
                if dry_run:
                    # Simulate TP hitting and SL hitting
                    if current_price >= state.target_1 and not state.tp1_hit:
                        logger.info("🎯 Simulated TP1 Hit!")
                        state.tp1_hit = True
                    elif current_price <= state.sl_level and not state.tp1_hit:
                        logger.info("🛑 Simulated SL Hit! Resetting bot.")
                        state.reset()
                        
                # Mudrex natively processes TP1 and SL for the initial quantity via exchange API logic. 
                # We just handle Trail Stop Phase 2 when tp1 is hit via the sync.
                if state.tp1_hit:
                    # Trail SL logic using 20 SMA
                    # If SMA moves up favorably, drag the stop up with it
                    current_sma = current_candle['sma']
                    logger.info(f"Trailing LONG Stop Loss over SMA level: {current_sma:.2f}")
                    
                    if not dry_run:
                        # Fetch Mudrex specific position and update SL
                        pos = mudrex_executor.get_open_position(MUDREX_SYMBOL)
                        if pos and float(pos.stoploss_price) < current_sma:
                             mudrex_executor.update_trailing_stoploss(pos.position_id, current_sma)
                             
                    # Explicit Exit Trap: If Price touches the SMA
                    if current_candle['low'] <= current_sma:
                        logger.info("🚨 Trailing SMA touched for LONG position. Exiting full remaining balance.")
                        if not dry_run and pos:
                            mudrex_executor.close_full_position(pos.position_id)
                        state.reset()

            # -------------------------------------------------------------
            # MANAGE OPEN SHORT
            # -------------------------------------------------------------
            elif state.in_short:
                state.pending_long = False
                state.pending_short = False
                
                if dry_run:
                    # Simulate TP hitting and SL hitting
                    if current_price <= state.target_1 and not state.tp1_hit:
                        logger.info("🎯 Simulated TP1 Hit!")
                        state.tp1_hit = True
                    elif current_price >= state.sl_level and not state.tp1_hit:
                        logger.info("🛑 Simulated SL Hit! Resetting bot.")
                        state.reset()
                        
                if state.tp1_hit:
                    current_sma = current_candle['sma']
                    logger.info(f"Trailing SHORT Stop Loss under SMA level: {current_sma:.2f}")
                    
                    if not dry_run:
                        pos = mudrex_executor.get_open_position(MUDREX_SYMBOL)
                        if pos and float(pos.stoploss_price) > current_sma:
                             mudrex_executor.update_trailing_stoploss(pos.position_id, current_sma)
                             
                    # Explicit Exit Trap: If Price touches the SMA
                    if current_candle['high'] >= current_sma:
                        logger.info("🚨 Trailing SMA touched for SHORT position. Exiting full remaining balance.")
                        if not dry_run and pos:
                            mudrex_executor.close_full_position(pos.position_id)
                        state.reset()

            # Save state explicitly at the end of every tick
            state.save()
            
            # Wait before next bar check
            time.sleep(15)
            
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
