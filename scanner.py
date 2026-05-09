import asyncio
from typing import Callable, Awaitable
from .exchange import load_top_symbols, fetch_ohlcv_df
from .strategy import score_signal


async def scan_market(settings: dict, progress: Callable[[str], Awaitable[None]] | None = None) -> list[tuple[dict, object]]:
    exchange_id = settings["exchange"]
    top_n = int(settings["top_n"])
    min_rr = float(settings["rr"])
    tp_mode = settings.get("tp_mode", "TP2")
    symbols = await load_top_symbols(exchange_id, top_n)
    results = []
    sem = asyncio.Semaphore(6)

    async def scan_one(symbol: str):
        async with sem:
            try:
                df = await fetch_ohlcv_df(exchange_id, symbol, "15m", 220)
                sig = score_signal(df, exchange_id, symbol, min_rr, tp_mode)
                if sig:
                    results.append((sig.to_dict(), df))
            except Exception:
                return

    if progress:
        await progress(f"Сканирую {len(symbols)} пар на {exchange_id.upper()}...")
    await asyncio.gather(*(scan_one(s) for s in symbols))
    results.sort(key=lambda x: (x[0]["probability"], x[0]["rr"]), reverse=True)
    return results
