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


async def fetch_ohlcv_df(exchange_id: str, symbol: str, timeframe: str = "15m", limit: int = 220) -> pd.DataFrame:
    def work():
        ex = make_exchange(exchange_id)
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["dt"] = pd.to_datetime(df["ts"], unit="ms")
        return df
    return await asyncio.to_thread(work)


async def place_market_order(exchange_id: str, symbol: str, side: str, amount: float, keys: dict[str, Any]):
    def work():
        ex = make_exchange(exchange_id, keys)
        ex.load_markets()
        order_side = "buy" if side.upper() == "LONG" else "sell"
        return ex.create_market_order(symbol, order_side, amount)
    return await asyncio.to_thread(work)
