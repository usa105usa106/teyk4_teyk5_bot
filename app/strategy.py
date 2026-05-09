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


def _find_pivots(df: pd.DataFrame, lookback: int = 4, tail: int = 120) -> list[tuple[int, str, float]]:
    """Lightweight pivot detector for heuristic Elliott-style direction.

    It is intentionally conservative: this is not a certified Elliott count,
    it is a market-structure approximation used as an optional filter.
    """
    data = df.tail(tail).reset_index(drop=True)
    pivots: list[tuple[int, str, float]] = []
    if len(data) < lookback * 2 + 10:
        return pivots
    for i in range(lookback, len(data) - lookback):
        h = float(data.loc[i, "high"])
        l = float(data.loc[i, "low"])
        left = data.iloc[i - lookback:i]
        right = data.iloc[i + 1:i + 1 + lookback]
        if h >= float(left["high"].max()) and h >= float(right["high"].max()):
            pivots.append((i, "H", h))
        if l <= float(left["low"].min()) and l <= float(right["low"].min()):
            pivots.append((i, "L", l))

    # keep alternating significant pivots, replacing weaker same-type pivots
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
    return cleaned[-8:]


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


def elliott_analysis(df: pd.DataFrame) -> dict:
    """Strict but lightweight Elliott filter.

    The bot does not force wave counts. It returns:
    - VALID when core Elliott rules pass;
    - POSSIBLE when the shape is close but needs confirmation;
    - INVALID/NEUTRAL when wave count should not be drawn or used.
    """
    pivots = _find_pivots(df, lookback=4, tail=140)
    base = {
        "direction": "NEUTRAL",
        "wave": "unclear",
        "score": 0,
        "reason": "волновая структура недостаточно чистая",
        "pivots": pivots,
        "structure": "INVALID",
        "pattern": "none",
        "plot_points": [],
    }
    if len(pivots) < 3:
        return base

    # Prefer a full 5-wave impulse: origin + 5 endpoints. We plot only endpoints 1-5.
    for pts in (pivots[-6:], pivots[-7:-1] if len(pivots) >= 7 else []):
        if len(pts) != 6:
            continue
        if _valid_bullish_impulse(pts):
            return {
                "direction": "LONG",
                "wave": "5-wave impulse",
                "score": 24,
                "reason": "валидная 5-волновая импульсная структура вверх",
                "pivots": pivots,
                "structure": "VALID",
                "pattern": "impulse5",
                "plot_points": pts[1:],
            }
        if _valid_bearish_impulse(pts):
            return {
                "direction": "SHORT",
                "wave": "5-wave impulse",
                "score": 24,
                "reason": "валидная 5-волновая импульсная структура вниз",
                "pivots": pivots,
                "structure": "VALID",
                "pattern": "impulse5",
                "plot_points": pts[1:],
            }
        if _possible_bullish_impulse(pts):
            return {
                "direction": "LONG",
                "wave": "possible 5-wave impulse",
                "score": 12,
                "reason": "возможная 5-волновая структура вверх, нужна проверка продолжения",
                "pivots": pivots,
                "structure": "POSSIBLE",
                "pattern": "impulse5",
                "plot_points": pts[1:],
            }
        if _possible_bearish_impulse(pts):
            return {
                "direction": "SHORT",
                "wave": "possible 5-wave impulse",
                "score": 12,
                "reason": "возможная 5-волновая структура вниз, нужна проверка продолжения",
                "pivots": pivots,
                "structure": "POSSIBLE",
                "pattern": "impulse5",
                "plot_points": pts[1:],
            }

    # ABC correction: exactly 3 alternating endpoints. A-B-C is drawn only with 3 points.
    last3 = pivots[-3:]
    types = "".join(p[1] for p in last3)
    prices = [p[2] for p in last3]
    if types == "LHL":
        # Downward correction inside broader bullish context: A low, B retrace, C low.
        valid = prices[2] <= prices[0] * 1.015
        return {
            "direction": "LONG",
            "wave": "A-B-C correction",
            "score": 18 if valid else 10,
            "reason": "3-волновая коррекция A-B-C вниз, возможен LONG continuation" if valid else "возможная ABC-коррекция вниз",
            "pivots": pivots,
            "structure": "VALID" if valid else "POSSIBLE",
            "pattern": "abc",
            "plot_points": last3,
        }
    if types == "HLH":
        # Upward correction inside broader bearish context: A high, B retrace, C high.
        valid = prices[2] >= prices[0] * 0.985
        return {
            "direction": "SHORT",
            "wave": "A-B-C correction",
            "score": 18 if valid else 10,
            "reason": "3-волновая коррекция A-B-C вверх, возможен SHORT continuation" if valid else "возможная ABC-коррекция вверх",
            "pivots": pivots,
            "structure": "VALID" if valid else "POSSIBLE",
            "pattern": "abc",
            "plot_points": last3,
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

    # Strict Elliott mode for signals: when Elliott is ON, signals are sent only
    # when the main direction agrees with Elliott bias and the structure is at
    # least POSSIBLE. Opposite or invalid/unclear wave context is skipped.
    if elliott_enabled:
        ell_dir = str(ell.get("direction", "NEUTRAL")).upper()
        ell_structure = str(ell.get("structure", "INVALID")).upper()
        if ell_dir != side or ell_structure not in {"VALID", "POSSIBLE"}:
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
    )
