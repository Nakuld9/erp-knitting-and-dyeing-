"""Perpetual inventory (FIFO) service."""
from app.services.inventory.service import (  # noqa: F401
    InsufficientStockError,
    balance,
    consume,
    receive,
    stock_on_hand,
    transfer_stock,
    weighted_average_cost,
)
