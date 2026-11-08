"""Perpetual inventory (FIFO) service."""
from app.services.inventory.service import (  # noqa: F401
    InsufficientStockError,
    balance,
    consume,
    receive,
    transfer_stock,
    weighted_average_cost,
)
