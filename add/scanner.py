import asyncio
from typing import Callable, Awaitable
from .exchange import load_top_symbols, resolve_symbols, fetch_ohlcv_df
from .strategy import score_signal


async def scan_market(settings: dict, progress: Callable[[str], Awaitable[None]] | None = None) -> list[tuple[dict, object]]:
    exchange_id = settings["exchange"]
    top_n = int(settings.get("top_n", 100))
    min_rr = float(settings["rr"])
    tp_mode = settings.get("tp_mode", "dynamic_tp")
    elliott_enabled = bool(settings.get("elliott_enabled", False))
    top_enabled = bool(settings.get("top_enabled", True))
    btc_eth_enabled = bool(settings.get("btc_eth_enabled", False))
    custom_coins = settings.get("custom_symbols") or []

    symbols: list[str] = []
    custom_resolved: set[str] = set()
    universe_mode = "empty"

    if btc_eth_enabled:
        symbols.extend(await resolve_symbols(exchange_id, ["BTC", "ETH"]))
        universe_mode = "BTC/ETH"
    elif top_enabled:
        symbols.extend(await load_top_symbols(exchange_id, top_n))
        universe_mode = f"Top-{top_n}"

    if custom_coins:
        custom_list = await resolve_symbols(exchange_id, custom_coins)
        custom_resolved = set(custom_list)
        symbols.extend(custom_list)
        if universe_mode == "empty":
            universe_mode = f"custom {len(custom_coins)}"
        else:
            universe_mode += f" + custom {len(custom_coins)}"

    symbols = list(dict.fromkeys(symbols))
    results = []
    sem = asyncio.Semaphore(6)

    async def scan_one(symbol: str):
        async with sem:
            try:
                df = await fetch_ohlcv_df(exchange_id, symbol, "15m", 220)
                sig = score_signal(df, exchange_id, symbol, min_rr, tp_mode, elliott_enabled=elliott_enabled)
                if sig:
                    d = sig.to_dict()
                    d["is_custom_symbol"] = symbol in custom_resolved
                    d["universe_mode"] = "custom" if symbol in custom_resolved else ("btc_eth" if btc_eth_enabled else "top")
                    results.append((d, df))
            except Exception:
                return

    if progress:
        await progress(f"Сканирую {len(symbols)} пар на {exchange_id.upper()} ({universe_mode})...")
    await asyncio.gather(*(scan_one(s) for s in symbols))
    results.sort(key=lambda x: (x[0]["probability"], x[0]["rr"]), reverse=True)
    return results
