# BTC Momentum Catcher - Python Bot

This repository contains the Python implementation of the **Stock Trade 360 - Momentum Catcher** TradingView Pine Script strategy, built specifically to run as a local bot against the Mudrex Futures API.

## Features
- Fetches market 15m OHLCV data natively from Bybit perpetuals to perfectly match Mudrex pricing.
- Calculates RSI(14) and SMA(20) locally using `pandas-ta`.
- Maintains open/close states safely in a local `bot_state.json` file.
- Automatically handles risk management through the Mudrex Python SDK.
  - Takes 50% profit exactly at a 1:1 risk-reward ratio.
  - Dynamically trails the stop-loss limit against the 20-period SMA for maximum trend capture.

## Setup Instructions

### 1. Requirements
Ensure you have Python 3.8+ installed.

### 2. Installations
Install the required dependencies, including the Mudrex SDK from GitHub:
```bash
pip install -r requirements.txt
pip install git+https://github.com/DecentralizedJM/mudrex-api-trading-python-sdk.git
```

### 3. API Keys
Create a `.env` file in the root directory and add your Mudrex Secret Key:
```env
MUDREX_API_SECRET=your_mudrex_api_secret_here
DRY_RUN=true
```

## Running the Bot
By default, the bot assumes `--dry-run` meaning it fetches live indicator data and prints when it *would* trade, but doesn't actually place the order. 

To run it:
```bash
python bot.py
```

To run it LIVE and place real trades on Mudrex, modify `.env`:
```env
DRY_RUN=false
```
