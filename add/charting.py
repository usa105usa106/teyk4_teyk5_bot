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
    x = list(range(len(data)))
    closes = data["close"].values
    ax.plot(x, closes, linewidth=1.6)

    entry = signal["entry"]
    entry_low = float(signal.get("entry_zone_low", entry))
    entry_high = float(signal.get("entry_zone_high", entry))
    stop = signal["stop"]
    tp = signal["take_profit"]
    side = signal["side"]

    ax.axhspan(min(entry_low, entry_high), max(entry_low, entry_high), color="red", alpha=0.18, label="Entry Zone")
    ax.axhline(entry, linestyle="--", linewidth=1.2, color="red", label="Signal Entry")
    ax.axhline(stop, linestyle="-", linewidth=1.2, color="red", label="Stop")
    ax.axhline(tp, linestyle="-", linewidth=1.4, color="green", label="Take Profit")

    ax.axhspan(min(entry, stop), max(entry, stop), color="red", alpha=0.14)
    ax.axhspan(min(entry, tp), max(entry, tp), color="green", alpha=0.08)

    ax.text(len(data)-1, entry, f" entry zone {entry_low}-{entry_high}", va="center", fontsize=9)
    ax.text(len(data)-1, stop, f" SL {stop}", va="center", fontsize=9)
    ax.text(len(data)-1, tp, f" TP {tp}", va="center", fontsize=9)

    if signal.get("elliott_enabled"):
        direction = signal.get("elliott_direction", "NEUTRAL")
        wave = signal.get("elliott_wave", "")
        y0 = float(closes[-1])
        y1 = tp if direction == side else (y0 + (tp - y0) * 0.45 if side == "LONG" else y0 - (y0 - tp) * 0.45)
        x0 = len(data) - 12
        x1 = len(data) - 1
        ax.annotate(
            f"Elliott: {direction}\n{wave}",
            xy=(x1, y1), xytext=(x0, y0),
            arrowprops=dict(arrowstyle="->", linewidth=1.6, color="blue"),
            fontsize=9, color="blue",
            bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.7),
        )

    ell_title = " | Elliott ON" if signal.get("elliott_enabled") else " | Elliott OFF"
    ax.set_title(f"{signal['symbol']} {side} | RR 1:{signal['rr']} | LONG {signal.get('long_probability', 0):.0f}% / SHORT {signal.get('short_probability', 0):.0f}% | CONF {signal.get('confidence_label', '-')}{ell_title}")
    ax.set_xlabel("Last 90 candles")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)
