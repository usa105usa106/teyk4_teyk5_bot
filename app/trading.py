import os
from .exchange import place_market_order
from .storage import load_api_keys


async def maybe_execute_trade(user_id: int, signal: dict, settings: dict) -> str:
    if not settings.get("auto_trade"):
        return "auto_off"
    if settings.get("trade_mode") != "live":
        return "paper_filled"
    if os.getenv("ALLOW_LIVE_TRADING", "0") != "1":
        return "live_blocked_env"
    keys_all = load_api_keys(user_id) or {}
    keys = keys_all.get(settings["exchange"])
    if not keys:
        return "missing_api_keys"

    # Minimal MVP sizing: user should replace with balance-based sizing after testing.
    # To avoid accidental oversized orders, default live execution is deliberately blocked unless amount is set manually in code/config.
    amount = float(settings.get("fixed_amount", 0) or 0)
    if amount <= 0:
        return "missing_fixed_amount"
    await place_market_order(settings["exchange"], signal["symbol"], signal["side"], amount, keys)
    return "live_order_sent"
