from pathlib import Path
import tempfile
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def make_signal_chart(df: pd.DataFrame, signal: dict) -> str:
    data = df.tail(90).copy()
    path = Path(tempfile.gettempdir()) / f"signal_{signal['exchange']}_{signal['symbol'].replace('/', '_').replace(':','_')}.png"
    fig, ax = plt.subplots(figsize=(11, 6))
    x = range(len(data))
    ax.plot(x, data["close"].values, linewidth=1.6)

    entry = signal["entry"]
    stop = signal["stop"]
    tp = signal["take_profit"]
    side = signal["side"]

    ax.axhline(entry, linestyle="--", linewidth=1.2, color="red", label="Entry")
    ax.axhline(stop, linestyle="-", linewidth=1.2, color="red", label="Stop")
    ax.axhline(tp, linestyle="-", linewidth=1.4, color="green", label="Take Profit")

    # Requested visual zones: red entry/SL zone, green max TP zone.
    ax.axhspan(min(entry, stop), max(entry, stop), color="red", alpha=0.14)
    ax.axhspan(min(entry, tp), max(entry, tp), color="green", alpha=0.08)

    ax.text(len(data)-1, entry, f" entry {entry}", va="center", fontsize=9)
    ax.text(len(data)-1, stop, f" SL {stop}", va="center", fontsize=9)
    ax.text(len(data)-1, tp, f" TP {tp}", va="center", fontsize=9)
    ax.set_title(f"{signal['symbol']} {side} | RR 1:{signal['rr']} | P={signal['probability']}%")
    ax.set_xlabel("Last 90 candles")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)
