from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Any

from .exchange import place_limit_order, place_market_order, place_stop_loss_order, place_take_profit_order
from .storage import load_api_keys


@dataclass
class ManagedTradePlan:
    entry_mode: str
    order_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    breakeven_trigger: float
    trailing_trigger: float
    trailing_distance: float
    dynamic_tp_enabled: bool
    runner_enabled: bool
    runner_size_pct: int
    exit_rules: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round_price(price: float) -> float:
    if price >= 1000:
        return round(price, 2)
    if price >= 1:
        return round(price, 4)
    return round(price, 8)


def build_trade_plan(signal: dict[str, Any], settings: dict[str, Any]) -> ManagedTradePlan:
    """Build a deterministic trade-management plan from signal + settings.

    The plan is used both for paper messages and live order placement.  It keeps
    signal generation separate from execution: the signal can be valid even if
    auto entry waits for a better price inside the Entry Zone.
    """
    side = str(signal["side"]).upper()
    entry = float(signal["entry"])
    stop = float(signal["stop"])
    tp = float(signal["take_profit"])
    risk = abs(entry - stop)

    entry_low = float(signal.get("entry_zone_low", min(entry, signal.get("entry_zone_high", entry))))
    entry_high = float(signal.get("entry_zone_high", max(entry, signal.get("entry_zone_low", entry))))
    midpoint = (entry_low + entry_high) / 2

    # Smart limit: prefer the stronger side of the entry zone, not a blind market buy/sell.
    if side == "LONG":
        # Long wants a slightly cheaper fill than midpoint when possible.
        smart_entry = max(entry_low, midpoint - (entry_high - entry_low) * 0.15)
        breakeven_trigger = smart_entry + risk * 2
        trailing_trigger = smart_entry + risk * 3
    else:
        # Short wants a slightly higher fill than midpoint when possible.
        smart_entry = min(entry_high, midpoint + (entry_high - entry_low) * 0.15)
        breakeven_trigger = smart_entry - risk * 2
        trailing_trigger = smart_entry - risk * 3

    trailing_distance = max(risk * 0.35, abs(tp - smart_entry) * 0.08)
    trade_management_mode = settings.get("trade_management_mode", "dynamic_tp")
    return ManagedTradePlan(
        entry_mode=settings.get("auto_entry_mode", "smart_limit"),
        order_type="limit",
        entry_price=_round_price(smart_entry),
        stop_loss=_round_price(stop),
        take_profit=_round_price(tp),
        breakeven_trigger=_round_price(breakeven_trigger),
        trailing_trigger=_round_price(trailing_trigger),
        trailing_distance=_round_price(trailing_distance),
        dynamic_tp_enabled=trade_management_mode in {"dynamic_tp", "runner"},
        runner_enabled=trade_management_mode == "runner",
        runner_size_pct=int(settings.get("runner_size_pct", 50)),
        exit_rules=[
            "TP reached",
            "trailing stop",
            "reversal signal",
            "structure break",
            "RSI divergence",
            "Elliott completion",
        ],
    )


async def maybe_execute_trade(user_id: int, signal: dict, settings: dict) -> dict[str, Any]:
    """Place or simulate a smart-limit entry and protective exits.

    Live order placement is intentionally gated by ALLOW_LIVE_TRADING=1 and a fixed
    amount.  In paper mode the function returns the exact auto-entry message data.
    """
    plan = build_trade_plan(signal, settings)
    result: dict[str, Any] = {"status": "auto_off", "plan": plan.to_dict(), "orders": []}

    # Custom watchlist coins are analysis-only. Auto-trading is allowed only for
    # automatic Top-N scanner signals.
    if signal.get("is_custom_symbol"):
        result["status"] = "custom_signal_only_no_auto_trade"
        return result

    if not settings.get("auto_trade"):
        return result

    # Elliott auto-trading rules follow the selected mode:
    # OFF    -> Elliott is ignored.
    # NORMAL -> Elliott must be aligned and at least POSSIBLE/VALID; confidence MEDIUM/HIGH.
    # HIGH   -> Elliott must be VALID and confidence HIGH.
    elliott_mode = str(settings.get("elliott_mode", "normal" if settings.get("elliott_enabled", True) else "off")).lower()
    if elliott_mode != "off":
        ell_dir = str(signal.get("elliott_direction", "NEUTRAL")).upper()
        ell_structure = str(signal.get("elliott_structure", "INVALID")).upper()
        conf = str(signal.get("confidence_label", "LOW")).upper()
        side = str(signal.get("side", "")).upper()
        if elliott_mode == "high":
            if ell_dir != side or ell_structure != "VALID" or conf != "HIGH":
                result["status"] = "auto_blocked_elliott_high_filter"
                return result
        else:
            if ell_dir != side or ell_structure not in {"VALID", "POSSIBLE"} or conf not in {"MEDIUM", "HIGH"}:
                result["status"] = "auto_blocked_elliott_normal_filter"
                return result

    if settings.get("trade_mode") != "live":
        result["status"] = "paper_limit_placed"
        result["orders"].append({"type": "limit", "price": plan.entry_price, "side": signal["side"]})
        result["orders"].append({"type": "stop_loss", "price": plan.stop_loss})
        result["orders"].append({"type": "take_profit", "price": plan.take_profit})
        return result

    if os.getenv("ALLOW_LIVE_TRADING", "0") != "1":
        result["status"] = "live_blocked_env"
        return result

    keys_all = load_api_keys(user_id) or {}
    keys = keys_all.get(settings["exchange"])
    if not keys:
        result["status"] = "missing_api_keys"
        return result

    amount = float(settings.get("fixed_amount", 0) or 0)
    if amount <= 0:
        result["status"] = "missing_fixed_amount"
        return result

    # 1) Entry limit order inside Entry Zone.
    entry_order = await place_limit_order(settings["exchange"], signal["symbol"], signal["side"], amount, plan.entry_price, keys)
    result["orders"].append({"type": "limit", "price": plan.entry_price, "raw": entry_order})

    # 2) Protective orders. Exact support varies by exchange; wrappers use CCXT params.
    try:
        sl_order = await place_stop_loss_order(settings["exchange"], signal["symbol"], signal["side"], amount, plan.stop_loss, keys)
        result["orders"].append({"type": "stop_loss", "price": plan.stop_loss, "raw": sl_order})
    except Exception as exc:
        result["orders"].append({"type": "stop_loss_error", "error": str(exc)})
    try:
        tp_order = await place_take_profit_order(settings["exchange"], signal["symbol"], signal["side"], amount, plan.take_profit, keys)
        result["orders"].append({"type": "take_profit", "price": plan.take_profit, "raw": tp_order})
    except Exception as exc:
        result["orders"].append({"type": "take_profit_error", "error": str(exc)})

    result["status"] = "live_limit_sent"
    return result
