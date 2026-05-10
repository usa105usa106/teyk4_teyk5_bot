from dataclasses import dataclass

EXCHANGES = ["binance", "bingx", "mexc"]
TOP_LIMITS = [10, 50, 100, 200, 300]
UNIVERSE_MODES = ["top", "btc_eth", "off"]
RR_VALUES = [3.0, 4.0, 5.0]
TAKE_PROFIT_MODES = ["fixed_tp", "dynamic_tp", "runner"]
TIMEFRAMES = ["15m", "1h"]

@dataclass
class Defaults:
    exchange: str = "mexc"
    top_n: int = 100
    top_enabled: bool = True
    btc_eth_enabled: bool = False
    custom_symbols: list = None
    rr: float = 3.0
    scan_minutes: int = 30
    bot_enabled: bool = True
    auto_trade: bool = False
    trade_mode: str = "paper"  # paper | live
    tp_mode: str = "dynamic_tp"
    trade_management_mode: str = "dynamic_tp"
    auto_entry_mode: str = "smart_limit"
    breakeven_enabled: bool = True
    trailing_enabled: bool = True
    runner_size_pct: int = 50
    risk_pct: float = 0.5
    leverage: int = 2
    # Elliott modes: off | normal | high
    # off = ignore Elliott; normal = soft confirmation/filter; high = strict VALID-only filter
    elliott_mode: str = "normal"
    elliott_enabled: bool = True  # backward compatibility; derived from elliott_mode != off
    premium_renderer: bool = False

    def __post_init__(self):
        if self.custom_symbols is None:
            self.custom_symbols = []

DEFAULTS = Defaults()
