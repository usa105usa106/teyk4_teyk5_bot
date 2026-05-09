import asyncio
from typing import Any
import ccxt
import pandas as pd


def make_exchange(exchange_id: str, keys: dict[str, Any] | None = None):
    if not hasattr(ccxt, exchange_id):
        raise ValueError(f"Биржа {exchange_id} не поддерживается ccxt")
    params: dict[str, Any] = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
    if keys:
        params.update({
            "apiKey": keys.get("api_key", ""),
            "secret": keys.get("api_secret", ""),
            "password": keys.get("password", ""),
        })
    ex = getattr(ccxt, exchange_id)(params)
    return ex


async def load_top_symbols(exchange_id: str, top_n: int) -> list[str]:
    def work():
        ex = make_exchange(exchange_id)
        markets = ex.load_markets()
        tickers = ex.fetch_tickers()
        rows = []
        for symbol, m in markets.items():
            if not m.get("active", True):
                continue
            if not (symbol.endswith("/USDT") or "/USDT:" in symbol):
                continue
            t = tickers.get(symbol, {})
            quote_volume = t.get("quoteVolume") or t.get("baseVolume") or 0
            if quote_volume:
                rows.append((symbol, float(quote_volume)))
        rows.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in rows[:top_n]]
    return await asyncio.to_thread(work)


async def resolve_symbols(exchange_id: str, coins: list[str]) -> list[str]:
    """Resolve short coin names like BTC/ETH/SOL into exchange symbols such as BTC/USDT.

    Preference is given to active USDT swap/perpetual markets. Unknown coins are ignored.
    """
    wanted = [c.strip().upper().replace("/USDT", "").replace("USDT", "") for c in coins if c.strip()]
    wanted = list(dict.fromkeys(wanted))

    def work():
        ex = make_exchange(exchange_id)
        markets = ex.load_markets()
        out = []
        for coin in wanted:
            matches = []
            for symbol, m in markets.items():
                base = str(m.get("base") or "").upper()
                quote = str(m.get("quote") or "").upper()
                if base != coin or quote != "USDT":
                    continue
                if not m.get("active", True):
                    continue
                # Prefer perpetual/swap markets, then any USDT market.
                score = 0
                if m.get("swap") or m.get("contract") or "/USDT:" in symbol:
                    score += 10
                if symbol.endswith("/USDT") or "/USDT:" in symbol:
                    score += 5
                matches.append((score, symbol))
            if matches:
                matches.sort(reverse=True)
                out.append(matches[0][1])
        return list(dict.fromkeys(out))
    return await asyncio.to_thread(work)


async def fetch_ohlcv_df(exchange_id: str, symbol: str, timeframe: str = "15m", limit: int = 220) -> pd.DataFrame:
    def work():
        ex = make_exchange(exchange_id)
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["dt"] = pd.to_datetime(df["ts"], unit="ms")
        return df
    return await asyncio.to_thread(work)


def _order_side(signal_side: str) -> str:
    return "buy" if signal_side.upper() == "LONG" else "sell"


def _exit_side(signal_side: str) -> str:
    return "sell" if signal_side.upper() == "LONG" else "buy"


async def place_market_order(exchange_id: str, symbol: str, side: str, amount: float, keys: dict[str, Any]):
    def work():
        ex = make_exchange(exchange_id, keys)
        ex.load_markets()
        return ex.create_market_order(symbol, _order_side(side), amount)
    return await asyncio.to_thread(work)


async def place_limit_order(exchange_id: str, symbol: str, side: str, amount: float, price: float, keys: dict[str, Any]):
    def work():
        ex = make_exchange(exchange_id, keys)
        ex.load_markets()
        return ex.create_limit_order(symbol, _order_side(side), amount, price)
    return await asyncio.to_thread(work)


async def place_stop_loss_order(exchange_id: str, symbol: str, signal_side: str, amount: float, stop_price: float, keys: dict[str, Any]):
    """Best-effort stop order wrapper. Test on paper/testnet before live.

    CCXT stop params differ by venue. This generic wrapper works on many swap
    venues but may require per-exchange tuning before real funds.
    """
    def work():
        ex = make_exchange(exchange_id, keys)
        ex.load_markets()
        params = {"stopPrice": stop_price, "reduceOnly": True, "triggerPrice": stop_price}
        return ex.create_order(symbol, "stop_market", _exit_side(signal_side), amount, None, params)
    return await asyncio.to_thread(work)


async def place_take_profit_order(exchange_id: str, symbol: str, signal_side: str, amount: float, tp_price: float, keys: dict[str, Any]):
    """Best-effort take-profit order wrapper. Test on paper/testnet before live."""
    def work():
        ex = make_exchange(exchange_id, keys)
        ex.load_markets()
        params = {"stopPrice": tp_price, "reduceOnly": True, "triggerPrice": tp_price}
        try:
            return ex.create_order(symbol, "take_profit_market", _exit_side(signal_side), amount, None, params)
        except Exception:
            return ex.create_order(symbol, "limit", _exit_side(signal_side), amount, tp_price, {"reduceOnly": True})
    return await asyncio.to_thread(work)
