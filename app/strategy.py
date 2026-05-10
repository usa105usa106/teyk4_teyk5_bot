from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class Signal:
    exchange: str
    symbol: str
    side: str
    entry: float
    entry_zone_low: float
    entry_zone_high: float
    stop: float
    take_profit: float
    rr: float
    probability: float
    long_probability: float
    short_probability: float
    confidence_score: float
    confidence_label: str
    reason: str
    timeframe: str = "15m"
    elliott_enabled: bool = False
    elliott_direction: str = "OFF"
    elliott_wave: str = ""
    elliott_score: float = 0.0
    elliott_reason: str = ""
    elliott_structure: str = "OFF"
    elliott_pattern: str = ""
    elliott_pivots: list | None = None
    elliott_phase: str = ""

    def to_dict(self):
        return self.__dict__.copy()


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["rsi"] = rsi(out["close"])
    out["atr"] = atr(out)
    out["vol_ma"] = out["volume"].rolling(30).mean()
    out["hh20"] = out["high"].rolling(20).max().shift(1)
    out["ll20"] = out["low"].rolling(20).min().shift(1)
    return out


def _find_pivots(df: pd.DataFrame, lookback: int = 5, tail: int = 140) -> list[tuple[int, str, float]]:
    """Conservative swing detector for Elliott annotations.

    The previous version was too aggressive and could label tiny market noise
    as A-B-C or 1-2-3-4-5. This version keeps only pivots with enough price
    distance and candle separation.
    """
    data = df.tail(tail).reset_index(drop=True)
    pivots: list[tuple[int, str, float]] = []
    if len(data) < lookback * 2 + 20:
        return pivots

    high_range = float(data["high"].max() - data["low"].min())
    close_med = float(data["close"].median())
    atr_med = float(atr(data).dropna().tail(40).median()) if not atr(data).dropna().empty else 0.0
    # Minimum visible swing: protects against labels on flat/noisy chop.
    min_move = max(atr_med * 1.15, high_range * 0.075, abs(close_med) * 0.004)
    min_sep = 4

    for i in range(lookback, len(data) - lookback):
        h = float(data.loc[i, "high"])
        l = float(data.loc[i, "low"])
        left = data.iloc[i - lookback:i]
        right = data.iloc[i + 1:i + 1 + lookback]
        if h >= float(left["high"].max()) and h >= float(right["high"].max()):
            pivots.append((i, "H", h))
        if l <= float(left["low"].min()) and l <= float(right["low"].max()):
            # Bug guard: use low min on the right, not high max.
            if l <= float(right["low"].min()):
                pivots.append((i, "L", l))

    # Keep alternating pivots. Replace same-type pivots by the more extreme one.
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
            if abs(p[2] - last[2]) >= min_move and abs(p[0] - last[0]) >= min_sep:
                cleaned.append(p)
            else:
                # Tiny opposite swing: ignore as noise unless it extends the last extreme.
                continue

    # Second pass: remove leftover tiny zigzags between neighbours.
    significant: list[tuple[int, str, float]] = []
    for p in cleaned:
        if not significant:
            significant.append(p)
            continue
        if abs(p[2] - significant[-1][2]) >= min_move and abs(p[0] - significant[-1][0]) >= min_sep:
            significant.append(p)

    return significant[-8:]


def _valid_bullish_impulse(points: list[tuple[int, str, float]]) -> bool:
    # origin + endpoints: 0(L), 1(H), 2(L), 3(H), 4(L), 5(H)
    if len(points) != 6 or "".join(p[1] for p in points) != "LHLHLH":
        return False
    p = [x[2] for x in points]
    wave1 = p[1] - p[0]
    wave3 = p[3] - p[2]
    wave5 = p[5] - p[4]
    return (
        p[2] > p[0] and        # wave 2 does not break start
        p[3] > p[1] and        # wave 3 exceeds wave 1
        p[4] > p[1] and        # wave 4 does not overlap wave 1 area (strict)
        p[5] > p[3] and        # wave 5 exceeds wave 3
        wave3 >= min(wave1, wave5)  # wave 3 is not shortest
    )


def _valid_bearish_impulse(points: list[tuple[int, str, float]]) -> bool:
    # origin + endpoints: 0(H), 1(L), 2(H), 3(L), 4(H), 5(L)
    if len(points) != 6 or "".join(p[1] for p in points) != "HLHLHL":
        return False
    p = [x[2] for x in points]
    wave1 = p[0] - p[1]
    wave3 = p[2] - p[3]
    wave5 = p[4] - p[5]
    return (
        p[2] < p[0] and        # wave 2 does not break start
        p[3] < p[1] and        # wave 3 exceeds wave 1 downward
        p[4] < p[1] and        # wave 4 does not overlap wave 1 area (strict)
        p[5] < p[3] and        # wave 5 exceeds wave 3 downward
        wave3 >= min(wave1, wave5)  # wave 3 is not shortest
    )


def _possible_bullish_impulse(points: list[tuple[int, str, float]]) -> bool:
    if len(points) != 6 or "".join(p[1] for p in points) != "LHLHLH":
        return False
    p = [x[2] for x in points]
    return p[3] > p[1] and p[5] >= p[3] * 0.985 and p[2] > p[0] * 0.985


def _possible_bearish_impulse(points: list[tuple[int, str, float]]) -> bool:
    if len(points) != 6 or "".join(p[1] for p in points) != "HLHLHL":
        return False
    p = [x[2] for x in points]
    return p[3] < p[1] and p[5] <= p[3] * 1.015 and p[2] < p[0] * 1.015




def _swing_quality(points: list[tuple[int, str, float]], min_move: float, min_sep: int = 4) -> bool:
    """Every segment must be large enough and separated enough to be plotted."""
    if len(points) < 2:
        return False
    for a, b in zip(points, points[1:]):
        if abs(b[2] - a[2]) < min_move or abs(b[0] - a[0]) < min_sep:
            return False
    return True


def _abc_quality(points: list[tuple[int, str, float]], min_move: float) -> tuple[bool, bool]:
    """Return (valid, possible) for a clean A-B-C correction.

    We require meaningful A/B/C legs and basic proportionality. This prevents
    the bot from drawing tiny A-B-C labels on sideways noise.
    """
    if len(points) != 3:
        return False, False
    a, b, c = points
    ab = abs(b[2] - a[2])
    bc = abs(c[2] - b[2])
    if ab < min_move or bc < min_move:
        return False, False
    ratio = bc / ab if ab else 0
    possible = 0.45 <= ratio <= 2.20
    valid = 0.62 <= ratio <= 1.80
    return valid, possible

def _validate_completed_abc(points: list[tuple[int, str, float]], min_move: float, close_now: float, atr_now: float) -> dict | None:
    """Validate a classic A-B-C correction and return Elliott state.

    A-B-C rules used here:
    - down correction = L-H-L after a previous bullish impulse/leg;
    - up correction = H-L-H after a previous bearish impulse/leg;
    - every leg must be meaningful, not just market noise;
    - C must reach/undercut A for down correction or reach/overcut A for up correction;
    - current close must show a bounce/rejection from C, otherwise the correction is only forming.
    """
    if len(points) != 3:
        return None
    types = "".join(p[1] for p in points)
    a, b, c = points
    ab = abs(b[2] - a[2])
    bc = abs(c[2] - b[2])
    if ab < min_move or bc < min_move:
        return None
    ratio = bc / ab if ab else 0.0
    if not (0.55 <= ratio <= 2.10):
        return None

    confirm = max(atr_now * 0.35, abs(c[2]) * 0.0018, min_move * 0.10)
    c_tolerance = max(min_move * 0.35, abs(a[2]) * 0.0015)

    if types == "LHL":
        # A-B-C down completed: A low -> B high -> C low. After C: new impulse UP.
        c_reached_a = c[2] <= a[2] + c_tolerance
        bounced = close_now > c[2] + confirm
        if c_reached_a and bounced:
            return {
                "direction": "LONG",
                "wave": "A-B-C correction down completed → expecting new impulse up",
                "score": 24,
                "reason": "ABC вниз завершена, C подтверждена отскоком, ожидается новый импульс вверх",
                "structure": "VALID",
                "pattern": "abc_down_completed",
                "plot_points": points,
                "phase": "abc_completed_expect_impulse_up",
            }
        if c_reached_a:
            return {
                "direction": "LONG",
                "wave": "A-B-C correction down forming → waiting C bounce",
                "score": 0,
                "reason": "ABC вниз ещё не подтверждена отскоком от C",
                "structure": "POSSIBLE",
                "pattern": "abc_down_forming",
                "plot_points": [],
                "phase": "correction_forming_down",
            }

    if types == "HLH":
        # A-B-C up completed: A high -> B low -> C high. After C: new impulse DOWN.
        c_reached_a = c[2] >= a[2] - c_tolerance
        rejected = close_now < c[2] - confirm
        if c_reached_a and rejected:
            return {
                "direction": "SHORT",
                "wave": "A-B-C correction up completed → expecting new impulse down",
                "score": 24,
                "reason": "ABC вверх завершена, C подтверждена отбоем, ожидается новый импульс вниз",
                "structure": "VALID",
                "pattern": "abc_up_completed",
                "plot_points": points,
                "phase": "abc_completed_expect_impulse_down",
            }
        if c_reached_a:
            return {
                "direction": "SHORT",
                "wave": "A-B-C correction up forming → waiting C rejection",
                "score": 0,
                "reason": "ABC вверх ещё не подтверждена отбоем от C",
                "structure": "POSSIBLE",
                "pattern": "abc_up_forming",
                "plot_points": [],
                "phase": "correction_forming_up",
            }
    return None


def elliott_analysis(df: pd.DataFrame) -> dict:
    """Strict classical Elliott 5+3 analysis.

    What is fixed in this version:
    - 5-wave impulse is always 1-2-3-4-5, with no repeated numbers.
    - A-B-C correction is always exactly A-B-C, with no floating letters.
    - After 5 waves UP the next expectation is A-B-C DOWN.
    - After 5 waves DOWN the next expectation is A-B-C UP.
    - After completed A-B-C DOWN the next expectation is a new impulse UP.
    - After completed A-B-C UP the next expectation is a new impulse DOWN.
    - POSSIBLE structures are NOT drawn and are NOT used for autotrading.
    """
    pivots = _find_pivots(df, lookback=5, tail=140)
    tail_df = df.tail(140).reset_index(drop=True)
    tail_range = float(tail_df["high"].max() - tail_df["low"].min()) if not tail_df.empty else 0.0
    close_med = float(tail_df["close"].median()) if not tail_df.empty else 0.0
    atr_vals = atr(tail_df).dropna() if not tail_df.empty else pd.Series(dtype=float)
    atr_med = float(atr_vals.tail(40).median()) if not atr_vals.empty else 0.0
    min_wave_move = max(atr_med * 1.55, tail_range * 0.115, abs(close_med) * 0.007)
    close_now = float(df["close"].iloc[-1])
    atr_now = float(atr(df).dropna().iloc[-1]) if not atr(df).dropna().empty else atr_med

    base = {
        "direction": "NEUTRAL",
        "wave": "unclear",
        "score": 0,
        "reason": "волновая структура недостаточно чистая",
        "pivots": pivots,
        "structure": "INVALID",
        "pattern": "none",
        "plot_points": [],
        "phase": "unclear",
        "tail_window": 140,
    }
    if len(pivots) < 3:
        return base

    # 1) First priority: completed A-B-C near the current market.
    # This is the most useful state for signals: after C, look for a new impulse.
    abc = _validate_completed_abc(pivots[-3:], min_wave_move, close_now, atr_now)
    if abc:
        abc.update({"pivots": pivots, "tail_window": 140})
        return abc

    # 2) Completed 5-wave impulse. Draw exactly 1-2-3-4-5 only when all rules pass.
    # After wave 5, expected move is correction A-B-C in the opposite direction.
    candidates = []
    if len(pivots) >= 6:
        candidates.append(pivots[-6:])
    # previous completed impulse before current forming correction
    if len(pivots) >= 7:
        candidates.append(pivots[-7:-1])

    for pts in candidates:
        if len(pts) != 6 or not _swing_quality(pts, min_wave_move, min_sep=7):
            continue
        xs = [p[0] for p in pts]
        if len(set(xs)) != 6 or xs != sorted(xs):
            continue

        if _valid_bullish_impulse(pts):
            return {
                "direction": "SHORT",
                "wave": "5-wave impulse up completed → expecting A-B-C down",
                "score": 18,
                "reason": "5 волн вверх завершены; по Elliott после 5 ожидается коррекция A-B-C вниз",
                "pivots": pivots,
                "structure": "VALID",
                "pattern": "impulse5_up_completed",
                "plot_points": pts[1:],
                "phase": "wave5_completed_expect_abc_down",
                "tail_window": 140,
            }

        if _valid_bearish_impulse(pts):
            return {
                "direction": "LONG",
                "wave": "5-wave impulse down completed → expecting A-B-C up",
                "score": 18,
                "reason": "5 волн вниз завершены; по Elliott после 5 ожидается коррекция A-B-C вверх",
                "pivots": pivots,
                "structure": "VALID",
                "pattern": "impulse5_down_completed",
                "plot_points": pts[1:],
                "phase": "wave5_completed_expect_abc_up",
                "tail_window": 140,
            }

    # 3) If a possible but unconfirmed structure exists, we expose it in text only.
    # No drawing, no autotrade, no signal confirmation.
    if len(pivots) >= 3:
        last3_types = "".join(p[1] for p in pivots[-3:])
        if last3_types in {"LHL", "HLH"}:
            return {
                **base,
                "structure": "POSSIBLE",
                "wave": "possible correction, waiting confirmation",
                "reason": "возможная коррекция, но нет подтверждения C",
                "phase": "correction_possible_unconfirmed",
            }

    return base

def _probability_from_points(points: float, opposite_points: float, min_rr: float, tp_mode: str) -> float:
    rr_penalty = max(0, (min_rr - 3) * 4)
    tp_bonus = 3 if tp_mode in {"dynamic_tp", "runner"} else 0
    conflict_penalty = max(0, opposite_points - 35) * 0.20
    raw = points + tp_bonus - rr_penalty - conflict_penalty
    return round(max(5, min(86, raw)), 1)


def _confidence_label(score: float) -> str:
    if score >= 78:
        return "HIGH"
    if score >= 64:
        return "MEDIUM"
    return "LOW"


def score_signal(df: pd.DataFrame, exchange: str, symbol: str, min_rr: float, tp_mode: str, elliott_enabled: bool = False) -> Signal | None:
    if len(df) < 210:
        return None
    d = enrich(df).dropna()
    if d.empty:
        return None
    last = d.iloc[-1]
    prev = d.iloc[-2]
    close = float(last.close)
    a = float(last.atr)
    if a <= 0:
        return None

    ell = elliott_analysis(d) if elliott_enabled else {"direction": "OFF", "wave": "", "score": 0, "reason": "", "pivots": [], "structure": "OFF", "pattern": "", "plot_points": []}

    long_points = 0
    long_reasons = []
    if last.ema20 > last.ema50 > last.ema200:
        long_points += 25; long_reasons.append("тренд выше EMA20/50/200")
    if close > float(last.hh20) or (prev.close <= prev.ema20 and close > last.ema20):
        long_points += 25; long_reasons.append("пробой/возврат выше EMA20")
    if 48 <= last.rsi <= 68:
        long_points += 18; long_reasons.append("RSI в рабочей зоне")
    if last.volume > last.vol_ma * 1.15:
        long_points += 17; long_reasons.append("объём выше среднего")
    if close > prev.close:
        long_points += 10; long_reasons.append("импульс последней свечи")

    short_points = 0
    short_reasons = []
    if last.ema20 < last.ema50 < last.ema200:
        short_points += 25; short_reasons.append("тренд ниже EMA20/50/200")
    if close < float(last.ll20) or (prev.close >= prev.ema20 and close < last.ema20):
        short_points += 25; short_reasons.append("пробой/возврат ниже EMA20")
    if 32 <= last.rsi <= 52:
        short_points += 18; short_reasons.append("RSI в рабочей зоне")
    if last.volume > last.vol_ma * 1.15:
        short_points += 17; short_reasons.append("объём выше среднего")
    if close < prev.close:
        short_points += 10; short_reasons.append("импульс последней свечи")

    if elliott_enabled:
        if ell["direction"] == "LONG":
            long_points += float(ell["score"]); long_reasons.append(f"Elliott: {ell['reason']}")
            short_points -= 6
        elif ell["direction"] == "SHORT":
            short_points += float(ell["score"]); short_reasons.append(f"Elliott: {ell['reason']}")
            long_points -= 6
        else:
            long_points -= 3; short_points -= 3

    long_probability = _probability_from_points(long_points, short_points, min_rr, tp_mode)
    short_probability = _probability_from_points(short_points, long_points, min_rr, tp_mode)

    side = None
    reasons: list[str] = []
    if long_points >= 68 and long_points >= short_points:
        side = "LONG"; reasons = long_reasons
        swing_low = float(d["low"].tail(12).min())
        stop = min(swing_low, close - 1.2 * a)
        risk = close - stop
        entry_zone_low = close - 0.25 * a
        entry_zone_high = close + 0.10 * a
        take_profit = close + risk * min_rr
    elif short_points >= 68:
        side = "SHORT"; reasons = short_reasons
        swing_high = float(d["high"].tail(12).max())
        stop = max(swing_high, close + 1.2 * a)
        risk = stop - close
        entry_zone_low = close - 0.10 * a
        entry_zone_high = close + 0.25 * a
        take_profit = close - risk * min_rr
    else:
        return None

    if risk <= 0:
        return None

    range80 = float(d["high"].tail(80).max() - d["low"].tail(80).min())
    if risk * min_rr > range80 * 1.35:
        return None

    probability = long_probability if side == "LONG" else short_probability
    confidence_score = round(min(100, max(0, probability + min(10, abs(long_points - short_points) * 0.25))), 1)
    confidence_label = _confidence_label(confidence_score)

    # Strict Elliott mode for signals and autotrading:
    # When Elliott is ON, the signal is allowed only when the main signal
    # agrees with Elliott direction AND the Elliott structure is VALID.
    # POSSIBLE is shown in text/status only, but is not enough for signals.
    if elliott_enabled:
        ell_dir = str(ell.get("direction", "NEUTRAL")).upper()
        ell_structure = str(ell.get("structure", "INVALID")).upper()
        if ell_dir != side or ell_structure != "VALID":
            return None

    if confidence_score < 64:
        return None

    reason_text = ", ".join(reasons) + f" | LONG {long_probability:.0f}% / SHORT {short_probability:.0f}%"
    if elliott_enabled:
        reason_text += f" | Elliott {ell['direction']} / {ell.get('structure', 'INVALID')} ({ell['wave']})"

    return Signal(
        exchange=exchange,
        symbol=symbol,
        side=side,
        entry=round(close, 8),
        entry_zone_low=round(float(entry_zone_low), 8),
        entry_zone_high=round(float(entry_zone_high), 8),
        stop=round(float(stop), 8),
        take_profit=round(float(take_profit), 8),
        rr=float(min_rr),
        probability=float(probability),
        long_probability=float(long_probability),
        short_probability=float(short_probability),
        confidence_score=float(confidence_score),
        confidence_label=confidence_label,
        reason=reason_text,
        elliott_enabled=bool(elliott_enabled),
        elliott_direction=str(ell["direction"]),
        elliott_wave=str(ell["wave"]),
        elliott_score=float(ell["score"]),
        elliott_reason=str(ell["reason"]),
        elliott_structure=str(ell.get("structure", "OFF")),
        elliott_pattern=str(ell.get("pattern", "")),
        elliott_pivots=ell.get("plot_points", []),
        elliott_phase=str(ell.get("phase", "")),
    )
