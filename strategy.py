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


def elliott_analysis(df: pd.DataFrame) -> dict:
    pivots = _find_pivots(df)
    if len(pivots) < 5:
        return {
            "direction": "NEUTRAL",
            "wave": "unclear",
            "score": 0,
            "reason": "волновая структура недостаточно чистая",
            "pivots": pivots,
        }

    last5 = pivots[-5:]
    types = "".join(p[1] for p in last5)
    prices = [p[2] for p in last5]

    # Impulse-like sequences: L-H-L-H-L can suggest next bullish leg if lows rise;
    # H-L-H-L-H can suggest next bearish leg if highs fall.
    if types == "LHLHL":
        higher_lows = prices[2] > prices[0] and prices[4] >= prices[2] * 0.985
        higher_high = prices[3] > prices[1]
        score = 18 if higher_lows and higher_high else 9
        direction = "LONG" if score >= 12 else "NEUTRAL"
        return {
            "direction": direction,
            "wave": "possible Wave 3/5 continuation",
            "score": score,
            "reason": "последние pivots похожи на восходящую импульсную структуру" if direction == "LONG" else "есть восходящие pivots, но структура слабая",
            "pivots": pivots,
        }
    if types == "HLHLH":
        lower_highs = prices[2] < prices[0] and prices[4] <= prices[2] * 1.015
        lower_low = prices[3] < prices[1]
        score = 18 if lower_highs and lower_low else 9
        direction = "SHORT" if score >= 12 else "NEUTRAL"
        return {
            "direction": direction,
            "wave": "possible Wave 3/5 continuation",
            "score": score,
            "reason": "последние pivots похожи на нисходящую импульсную структуру" if direction == "SHORT" else "есть нисходящие pivots, но структура слабая",
            "pivots": pivots,
        }

    # fallback: compare recent pivot slope
    first, last = pivots[-5], pivots[-1]
    if last[2] > first[2] * 1.01:
        direction = "LONG"; score = 8; reason = "волновой наклон вверх, но без чистого паттерна 5 волн"
    elif last[2] < first[2] * 0.99:
        direction = "SHORT"; score = 8; reason = "волновой наклон вниз, но без чистого паттерна 5 волн"
    else:
        direction = "NEUTRAL"; score = 0; reason = "волновой фильтр нейтрален"
    return {"direction": direction, "wave": "structure slope", "score": score, "reason": reason, "pivots": pivots}


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

    ell = elliott_analysis(d) if elliott_enabled else {"direction": "OFF", "wave": "", "score": 0, "reason": "", "pivots": []}

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
    if confidence_score < 64:
        return None

    reason_text = ", ".join(reasons) + f" | LONG {long_probability:.0f}% / SHORT {short_probability:.0f}%"
    if elliott_enabled:
        reason_text += f" | Elliott {ell['direction']} ({ell['wave']})"

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
    )
