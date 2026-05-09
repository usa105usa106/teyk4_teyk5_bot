from dataclasses import dataclass

EXCHANGES = ["binance", "bingx", "mexc"]
TOP_LIMITS = [10, 50, 100, 200, 300]
RR_VALUES = [3.0, 4.0, 5.0]
TAKE_PROFIT_MODES = ["TP2", "TP3", "TRAIL"]
TIMEFRAMES = ["15m", "1h"]

@dataclass
class Defaults:
    exchange: str = "binance"
    top_n: int = 100
    rr: float = 3.0
    scan_minutes: int = 30
    bot_enabled: bool = True
    auto_trade: bool = False
    trade_mode: str = "paper"  # paper | live
    tp_mode: str = "TP2"
    risk_pct: float = 0.5
    leverage: int = 2

DEFAULTS = Defaults()
