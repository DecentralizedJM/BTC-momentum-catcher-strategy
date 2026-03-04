"""
Mudrex Executor Helper
Wraps the Mudrex Python SDK to place orders and manage trailing stops.
"""

import os
import logging
from mudrex import MudrexClient
from mudrex.exceptions import MudrexAPIError

logger = logging.getLogger(__name__)

class MudrexExecutor:
    def __init__(self):
        api_secret = os.getenv("MUDREX_API_SECRET")
        if not api_secret:
            raise ValueError("MUDREX_API_SECRET environment variable is missing.")
            
        self.client = MudrexClient(api_secret=api_secret)
        
    def get_open_position(self, symbol: str):
        """Returns the open position object for a symbol, or None if no position is open."""
        try:
            positions = self.client.positions.list_open()
            for pos in positions:
                if pos.symbol == symbol:
                    return pos
            return None
        except Exception as e:
            logger.error(f"Error fetching open positions: {e}")
            return None

    def place_market_order(self, symbol: str, side: str, quantity: float, leverage: int, stoploss: float, takeprofit: float):
        """Places a market order with initial SL and TP."""
        try:
            # First set the leverage
            self.client.leverage.set(symbol, leverage=str(leverage), margin_type="ISOLATED")
            
            # Place the order
            order = self.client.orders.create_market_order(
                symbol=symbol,
                side=side,
                quantity=str(quantity),
                leverage=str(leverage),
                stoploss_price=str(stoploss),
                takeprofit_price=str(takeprofit)
            )
            logger.info(f"Market order placed successfully: {order.order_id}")
            return order
        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
            return None

    def close_partial_position(self, position_id: str, quantity: float):
        """Closes a specific portion of an open position for TP1."""
        try:
            res = self.client.positions.close_partial(position_id, quantity=str(quantity))
            logger.info(f"Partially closed position {position_id} for {quantity} contracts.")
            return res
        except Exception as e:
            logger.error(f"Error partially closing position: {e}")
            return None

    def close_full_position(self, position_id: str):
        """Completely closes an open position."""
        try:
            res = self.client.positions.close(position_id)
            logger.info(f"Fully closed position {position_id}.")
            return res
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return None

    def update_trailing_stoploss(self, position_id: str, new_stoploss: float):
        """Updates the stoploss of an existing position to implement the SMA trail."""
        try:
            res = self.client.positions.set_stoploss(position_id, stoploss_price=str(new_stoploss))
            logger.info(f"Stoploss updated to {new_stoploss} for position {position_id}.")
            return res
        except Exception as e:
            logger.error(f"Error updating stoploss: {e}")
            return None
