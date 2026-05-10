from pathlib import Path
import tempfile
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd


def _fmt_price(v: float) -> str:
    v = float(v)
    if abs(v) >= 1000:
        return f"{v:,.2f}".replace(",", " ")
    if abs(v) >= 1:
        return f"{v:.4f}"
    return f"{v:.6f}"


def _find_pivots(data: pd.DataFrame, lookback: int = 4, max_points: int = 8) -> list[tuple[int, str, float]]:
    """Find alternating swing highs/lows for chart-only Elliott annotation."""
    pivots: list[tuple[int, str, float]] = []
    if len(data) < lookback * 2 + 10:
        return pivots
    for i in range(lookback, len(data) - lookback):
        h = float(data.iloc[i]["high"])
        l = float(data.iloc[i]["low"])
        left = data.iloc[i - lookback:i]
        right = data.iloc[i + 1:i + 1 + lookback]
        if h >= float(left["high"].max()) and h >= float(right["high"].max()):
            pivots.append((i, "H", h))
        if l <= float(left["low"].min()) and l <= float(right["low"].min()):
            pivots.append((i, "L", l))

    cleaned: list[tuple[int, str, float]] = []
    for p in pivots:
        if not cleaned:
            cleaned.append(p)
            continue
        last = cleaned[-1]
        if p[1] == last[1]:
            if (p[1] == "H" and p[2] > last[2]) or (p[1] == "L" and p[2] < last[2]):
                cleaned[-1] = p
        else:
            cleaned.append(p)
    return cleaned[-max_points:]


def _draw_candles(ax, data: pd.DataFrame):
    width = 0.62
    for i, row in enumerate(data.itertuples()):
        open_, high, low, close = float(row.open), float(row.high), float(row.low), float(row.close)
        up = close >= open_
        color = "#22c55e" if up else "#ef4444"
        ax.vlines(i, low, high, color=color, linewidth=1.0, alpha=0.95, zorder=2)
        body_low = min(open_, close)
        body_h = abs(close - open_)
        if body_h <= 0:
            body_h = max((high - low) * 0.025, abs(close) * 0.00002)
        ax.add_patch(Rectangle((i - width / 2, body_low), width, body_h,
                               facecolor=color, edgecolor=color, alpha=0.95, linewidth=0.8, zorder=3))


def _draw_volume(ax, data: pd.DataFrame):
    """Draw volume bars under candles. If no volume column exists, do nothing safely."""
    if "volume" not in data.columns:
        ax.text(0.5, 0.5, "Volume: no data", transform=ax.transAxes, ha="center", va="center",
                fontsize=9, color="#94a3b8")
        return
    vols = pd.to_numeric(data["volume"], errors="coerce").fillna(0.0)
    if float(vols.max()) <= 0:
        ax.text(0.5, 0.5, "Volume: no data", transform=ax.transAxes, ha="center", va="center",
                fontsize=9, color="#94a3b8")
        return

    avg = vols.rolling(20, min_periods=1).mean()
    for i, row in enumerate(data.itertuples()):
        up = float(row.close) >= float(row.open)
        color = "#22c55e" if up else "#ef4444"
        ax.bar(i, float(vols.iloc[i]), width=0.62, color=color, alpha=0.55, linewidth=0, zorder=2)
    ax.plot(range(len(data)), avg, color="#facc15", linewidth=1.2, alpha=0.9, label="Avg Vol 20", zorder=3)

    last_vol = float(vols.iloc[-1])
    last_avg = float(avg.iloc[-1]) if float(avg.iloc[-1]) > 0 else 1.0
    diff_pct = (last_vol / last_avg - 1.0) * 100.0
    ax.text(0.012, 0.88, f"Volume {last_vol:,.0f} | Avg20 {last_avg:,.0f} | {diff_pct:+.0f}%",
            transform=ax.transAxes, ha="left", va="top", fontsize=8.5, color="#e5e7eb",
            bbox=dict(boxstyle="round,pad=0.28", fc="#111827", ec="#334155", alpha=0.82), zorder=5)

    ax.set_ylim(0, float(vols.max()) * 1.28)
    ax.yaxis.tick_right()
    ax.tick_params(axis="x", colors="#cbd5e1", labelsize=8)
    ax.tick_params(axis="y", colors="#94a3b8", labelsize=8)
    ax.grid(True, alpha=0.14, color="#94a3b8")
    for spine in ax.spines.values():
        spine.set_color("#334155")


def _right_label(ax, x: float, y: float, text: str, fc: str, color: str = "white"):
    ax.text(x, y, text, ha="left", va="center", fontsize=9, color=color,
            bbox=dict(boxstyle="round,pad=0.28", fc=fc, ec=fc, alpha=0.94),
            clip_on=False, zorder=12)


def _draw_elliott(ax, data: pd.DataFrame, signal: dict):
    """Draw Elliott only from validated strategy output.

    Rules:
    - 5-wave impulse is labelled exactly 1-2-3-4-5, never with repeats.
    - A-B-C correction is labelled exactly A-B-C.
    - After completed 5 up/down the arrow points to expected A-B-C correction.
    - After completed A-B-C the arrow points to expected new impulse.
    - If points are not clean or not visible, do not force labels.
    """
    if not signal.get("elliott_enabled"):
        return

    direction = str(signal.get("elliott_direction", "NEUTRAL")).upper()
    wave = str(signal.get("elliott_wave", ""))
    structure = str(signal.get("elliott_structure", "INVALID")).upper()
    pattern = str(signal.get("elliott_pattern", ""))
    phase = str(signal.get("elliott_phase", ""))
    score = float(signal.get("elliott_score", 0) or 0)
    points = signal.get("elliott_pivots") or []

    if structure not in {"VALID", "POSSIBLE"} or direction not in {"LONG", "SHORT"}:
        ax.text(0.015, 0.94,
                "🌊 Elliott ON\nStructure: INVALID/UNCLEAR\nWave count not drawn",
                transform=ax.transAxes, ha="left", va="top", fontsize=9, color="#e5e7eb",
                bbox=dict(boxstyle="round,pad=0.45", fc="#111827", ec="#ef4444", alpha=0.88), zorder=14)
        return

    box_ec = "#22c55e" if structure == "VALID" else "#facc15"
    ax.text(0.015, 0.94,
            f"🌊 Elliott ON\nBias: {direction}\nStructure: {structure}\nPattern: {wave}\nScore impact: +{score:.0f}",
            transform=ax.transAxes, ha="left", va="top", fontsize=9, color="#e5e7eb",
            bbox=dict(boxstyle="round,pad=0.45", fc="#111827", ec=box_ec, alpha=0.88), zorder=14)

    # Strategy pivot x values are relative to a 140-candle tail. The plotted
    # chart shows 90 candles, so subtract 50 to land on the visible window.
    visible_pts = []
    if points:
        raw_pts = [(int(p[0]), str(p[1]), float(p[2])) for p in points]
        offset = max(0, 140 - len(data))
        visible_pts = [(x - offset, typ, y) for x, typ, y in raw_pts if 0 <= x - offset < len(data)]

    # Draw a validated sequence only when all required points are visible and unique.
    if pattern.startswith("full_") and len(visible_pts) == 8:
        xs = [p[0] for p in visible_pts]
        if len(set(xs)) == 8 and xs == sorted(xs):
            impulse = visible_pts[:5]
            corr = visible_pts[5:]
            impulse_color = "#22c55e" if "bullish" in pattern else "#ef4444"
            corr_color = "#facc15"
            ax.plot([p[0] for p in impulse], [p[2] for p in impulse], color=impulse_color, linewidth=2.7, linestyle="-", zorder=8)
            ax.plot([p[0] for p in corr], [p[2] for p in corr], color=corr_color, linewidth=2.7, linestyle="-", zorder=9)
            for label, (x, typ, y) in zip(["1", "2", "3", "4", "5"], impulse):
                off = (ax.get_ylim()[1] - ax.get_ylim()[0]) * (0.035 if typ == "H" else -0.045)
                ax.text(x, y + off, label, color=impulse_color, fontsize=12, fontweight="bold",
                        ha="center", va="center", zorder=10,
                        bbox=dict(boxstyle="circle,pad=0.18", fc="#0b1220", ec=impulse_color, alpha=0.92))
            for label, (x, typ, y) in zip(["A", "B", "C"], corr):
                off = (ax.get_ylim()[1] - ax.get_ylim()[0]) * (0.035 if typ == "H" else -0.045)
                ax.text(x, y + off, label, color=corr_color, fontsize=12, fontweight="bold",
                        ha="center", va="center", zorder=10,
                        bbox=dict(boxstyle="circle,pad=0.18", fc="#0b1220", ec=corr_color, alpha=0.92))
    elif pattern.startswith("impulse5") and len(visible_pts) == 5:
        xs = [p[0] for p in visible_pts]
        if len(set(xs)) == 5 and xs == sorted(xs):
            ys = [p[2] for p in visible_pts]
            impulse_color = "#22c55e" if "up" in pattern else "#ef4444"
            ls = "-" if structure == "VALID" else "--"
            ax.plot(xs, ys, color=impulse_color, linewidth=2.4, linestyle=ls, zorder=8)
            for label, (x, typ, y) in zip(["1", "2", "3", "4", "5"], visible_pts):
                off = (ax.get_ylim()[1] - ax.get_ylim()[0]) * (0.035 if typ == "H" else -0.045)
                ax.text(x, y + off, label, color=impulse_color, fontsize=12, fontweight="bold",
                        ha="center", va="center", zorder=10,
                        bbox=dict(boxstyle="circle,pad=0.18", fc="#0b1220", ec=impulse_color, alpha=0.92))
    elif pattern.startswith("abc") and len(visible_pts) == 3:
        xs = [p[0] for p in visible_pts]
        if len(set(xs)) == 3 and xs == sorted(xs):
            ys = [p[2] for p in visible_pts]
            ls = "-" if structure == "VALID" else "--"
            ax.plot(xs, ys, color="#facc15", linewidth=2.4, linestyle=ls, zorder=8)
            for label, (x, typ, y) in zip(["A", "B", "C"], visible_pts):
                off = (ax.get_ylim()[1] - ax.get_ylim()[0]) * (0.035 if typ == "H" else -0.045)
                ax.text(x, y + off, label, color="#facc15", fontsize=12, fontweight="bold",
                        ha="center", va="center", zorder=10,
                        bbox=dict(boxstyle="circle,pad=0.18", fc="#0b1220", ec="#facc15", alpha=0.92))

    # Projected move. For completed 5-wave impulse this is the expected ABC
    # correction. For completed ABC this is the expected new impulse.
    last_x = len(data) - 1
    last_close = float(data["close"].iloc[-1])
    tp = float(signal["take_profit"])
    stop = float(signal.get("stop", last_close))
    is_up = direction == "LONG"
    start_x = last_x + 1.8
    end_x = last_x + 10.5
    start_y = last_close
    # Use TP as target when aligned with trade side; for post-5 correction this
    # is still the signal's expected direction after Elliott filtering.
    end_y = tp if is_up else tp

    ax.annotate("", xy=(end_x, end_y), xytext=(start_x, start_y),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=34, linewidth=8.0,
                                color="#1d4ed8", alpha=0.20, linestyle="--"),
                zorder=8)
    ax.annotate("", xy=(end_x, end_y), xytext=(start_x, start_y),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=28, linewidth=3.8,
                                color="#3b82f6", alpha=0.98, linestyle="--"),
                zorder=11)
    label_y = start_y + (end_y - start_y) * 0.55
    label = "ELLIOTT\nEXPECTED MOVE"
    if phase.startswith("wave5_completed") or phase.startswith("possible_wave5"):
        label = "ELLIOTT\nEXPECTED A-B-C"
    elif phase.startswith("abc_completed"):
        label = "ELLIOTT\nNEW IMPULSE"
    ax.text(start_x + 2.4, label_y, label, ha="left", va="center",
            fontsize=11, fontweight="bold", color="#60a5fa", zorder=12,
            bbox=dict(boxstyle="round,pad=0.35", fc="#0b1220", ec="#1d4ed8", alpha=0.72))

def _make_simple_signal_chart(df: pd.DataFrame, signal: dict) -> str:
    """Low-resource chart: close line + Entry/SL/TP levels, no candles/volume/Elliott detail."""
    data = df.tail(90).copy().reset_index(drop=True)
    path = Path(tempfile.gettempdir()) / f"signal_simple_{signal['exchange']}_{signal['symbol'].replace('/', '_').replace(':','_')}.png"
    entry = float(signal["entry"])
    entry_low = float(signal.get("entry_zone_low", entry))
    entry_high = float(signal.get("entry_zone_high", entry))
    stop = float(signal["stop"])
    tp = float(signal["take_profit"])
    side = str(signal.get("side", "LONG"))

    fig, ax = plt.subplots(figsize=(10.5, 5.8))
    fig.patch.set_facecolor("#0b1220")
    ax.set_facecolor("#0f172a")
    closes = pd.to_numeric(data["close"], errors="coerce").fillna(method="ffill").fillna(method="bfill")
    x = list(range(len(data)))
    ax.plot(x, closes, color="#93c5fd", linewidth=1.8, zorder=3)

    ax.axhspan(min(entry_low, entry_high), max(entry_low, entry_high), color="#22c55e", alpha=0.18, zorder=0)
    ax.axhspan(min(entry, stop), max(entry, stop), color="#ef4444", alpha=0.18, zorder=0)
    ax.axhline(tp, color="#22c55e", linewidth=1.4)
    ax.axhline(entry, color="#22c55e", linewidth=1.2, linestyle="--")
    ax.axhline(stop, color="#ef4444", linewidth=1.4)

    right_x = len(data) - 1
    ax.text(right_x, tp, f" TP {_fmt_price(tp)} ", color="white", va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.25", fc="#16a34a", ec="#16a34a", alpha=0.95), clip_on=False)
    ax.text(right_x, entry, f" ENTRY {_fmt_price(entry)} ", color="white", va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.25", fc="#22c55e", ec="#22c55e", alpha=0.95), clip_on=False)
    ax.text(right_x, stop, f" SL {_fmt_price(stop)} ", color="white", va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.25", fc="#dc2626", ec="#dc2626", alpha=0.95), clip_on=False)

    ax.text(0.015, 0.95, "SIMPLE RENDERER\nLow-resource mode", transform=ax.transAxes,
            ha="left", va="top", fontsize=9, color="#e5e7eb",
            bbox=dict(boxstyle="round,pad=0.35", fc="#111827", ec="#334155", alpha=0.86))
    ax.set_title(f"{signal['symbol']} {side} | RR 1:{signal.get('rr')} | Simple chart", fontsize=13, color="#f8fafc", pad=10)
    ax.yaxis.tick_right()
    ax.tick_params(axis="x", colors="#cbd5e1")
    ax.tick_params(axis="y", colors="#cbd5e1")
    for spine in ax.spines.values():
        spine.set_color("#334155")
    ax.grid(True, alpha=0.16, color="#94a3b8")
    y_min = min(float(closes.min()), stop, entry_low, tp)
    y_max = max(float(closes.max()), stop, entry_high, tp)
    pad = (y_max - y_min) * 0.13 if y_max > y_min else abs(y_max) * 0.02
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlim(0, len(data) + 10)
    fig.tight_layout(pad=0.9)
    fig.savefig(path, dpi=115, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def make_signal_chart(df: pd.DataFrame, signal: dict, premium: bool = True) -> str:
    if not premium:
        return _make_simple_signal_chart(df, signal)
    data = df.tail(90).copy().reset_index(drop=True)
    path = Path(tempfile.gettempdir()) / f"signal_{signal['exchange']}_{signal['symbol'].replace('/', '_').replace(':','_')}.png"

    entry = float(signal["entry"])
    entry_low = float(signal.get("entry_zone_low", entry))
    entry_high = float(signal.get("entry_zone_high", entry))
    stop = float(signal["stop"])
    tp = float(signal["take_profit"])
    side = str(signal.get("side", "LONG"))

    fig, (ax, ax_vol) = plt.subplots(
        2, 1, figsize=(13.5, 8.4), sharex=True,
        gridspec_kw={"height_ratios": [5.8, 1.25], "hspace": 0.03}
    )
    fig.patch.set_facecolor("#0b1220")
    ax.set_facecolor("#0f172a")
    ax_vol.set_facecolor("#0f172a")

    _draw_candles(ax, data)
    x0 = 0
    x1 = len(data) + 15
    right_x = len(data) + 1.7

    # Risk/reward zones.
    ax.axhspan(min(entry_low, entry_high), max(entry_low, entry_high), xmin=0.62, xmax=0.93,
               color="#22c55e", alpha=0.16, zorder=0)
    ax.axhspan(min(entry, stop), max(entry, stop), xmin=0.62, xmax=0.93,
               color="#ef4444", alpha=0.22, zorder=0)
    ax.axhspan(min(entry, tp), max(entry, tp), xmin=0.62, xmax=0.93,
               color="#22c55e", alpha=0.11, zorder=0)

    ax.hlines([tp], x0, x1, colors="#22c55e", linestyles="-", linewidth=1.4, zorder=4)
    ax.hlines([entry], x0, x1, colors="#22c55e", linestyles="--", linewidth=1.2, zorder=4)
    ax.hlines([entry_low, entry_high], x0, x1, colors="#22c55e", linestyles=":", linewidth=1.0, zorder=4)
    ax.hlines([stop], x0, x1, colors="#ef4444", linestyles="-", linewidth=1.4, zorder=4)

    # Green entry frame.
    entry_box_low = min(entry_low, entry_high)
    entry_box_high = max(entry_low, entry_high)
    ax.add_patch(Rectangle((int(len(data) * 0.62), entry_box_low),
                           len(data) * 0.31, max(entry_box_high - entry_box_low, abs(entry) * 0.0001),
                           fill=False, edgecolor="#22c55e", linewidth=1.6, zorder=6))

    # Right labels, automatically spaced.
    yrange = max(float(data["high"].max()), tp, entry_high, stop) - min(float(data["low"].min()), tp, entry_low, stop)
    min_gap = yrange * 0.045 if yrange > 0 else abs(entry) * 0.004
    label_levels = [(tp, f"TP {_fmt_price(tp)}", "#16a34a"),
                    (entry_high, f"Entry high {_fmt_price(entry_high)}", "#16a34a"),
                    (entry, f"Entry {_fmt_price(entry)}", "#22c55e"),
                    (entry_low, f"Entry low {_fmt_price(entry_low)}", "#16a34a"),
                    (stop, f"SL {_fmt_price(stop)}", "#dc2626")]
    placed: list[float] = []
    for y, text, fc in sorted(label_levels, key=lambda t: t[0], reverse=True):
        yy = y
        while any(abs(yy - p) < min_gap for p in placed):
            yy -= min_gap
        placed.append(yy)
        _right_label(ax, right_x, yy, text, fc)

    zone_x = int(len(data) * 0.75)
    ax.text(zone_x, (entry_box_low + entry_box_high) / 2,
            f"ENTRY ZONE\n{_fmt_price(entry_low)} – {_fmt_price(entry_high)}",
            ha="center", va="center", fontsize=10, color="#dcfce7", zorder=7,
            bbox=dict(boxstyle="round,pad=0.35", fc="#14532d", ec="#22c55e", alpha=0.82))

    _draw_elliott(ax, data, signal)

    ell = "Elliott ON" if signal.get("elliott_enabled") else "Elliott OFF"
    title = (f"{signal['symbol']} {side} | RR 1:{signal.get('rr')} | "
             f"LONG {float(signal.get('long_probability', 0)):.0f}% / SHORT {float(signal.get('short_probability', 0)):.0f}% | "
             f"CONF {signal.get('confidence_label', '-')} | {ell}")
    ax.set_title(title, fontsize=14, color="#f8fafc", pad=12)

    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.tick_params(axis="x", colors="#cbd5e1")
    ax.tick_params(axis="y", colors="#cbd5e1")
    for spine in ax.spines.values():
        spine.set_color("#334155")
    ax.grid(True, alpha=0.18, color="#94a3b8")
    ax.set_ylabel("Price", color="#cbd5e1")

    y_min = min(float(data["low"].min()), stop, entry_low, tp)
    y_max = max(float(data["high"].max()), stop, entry_high, tp)
    pad = (y_max - y_min) * 0.12 if y_max > y_min else abs(y_max) * 0.02
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlim(-1, len(data) + 16)

    legend_items = [
        Rectangle((0, 0), 1, 1, facecolor="#22c55e", alpha=0.18, edgecolor="#22c55e", label="Entry Zone"),
        Rectangle((0, 0), 1, 1, facecolor="#ef4444", alpha=0.22, edgecolor="#ef4444", label="Risk Zone"),
        Rectangle((0, 0), 1, 1, facecolor="#22c55e", alpha=0.11, edgecolor="#22c55e", label="Reward Zone"),
    ]
    leg = ax.legend(handles=legend_items, loc="lower left", framealpha=0.86, fontsize=9)
    leg.get_frame().set_facecolor("#0f172a")
    leg.get_frame().set_edgecolor("#334155")
    for text in leg.get_texts():
        text.set_color("#e5e7eb")

    _draw_volume(ax_vol, data)
    ax_vol.set_xlabel("Last 90 candles", color="#cbd5e1")
    ax_vol.set_ylabel("Volume", color="#cbd5e1")

    fig.tight_layout(pad=1.0)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)
