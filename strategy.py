from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class Signal:
    exchange: str
    symbol: str
    side: str
    entry: float
    stop: float
    take_profit: float
    rr: float
    probability: float
    reason: str
    timeframe: str = "15m"

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


def score_signal(df: pd.DataFrame, exchange: str, symbol: str, min_rr: float, tp_mode: str) -> Signal | None:
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

    # LONG setup: trend + breakout + volume/rsi confirmation.
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

    # SHORT setup.
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

    side = None
    points = 0
    reasons: list[str] = []
    if long_points >= 68 and long_points >= short_points:
        side = "LONG"; points = long_points; reasons = long_reasons
        swing_low = float(d["low"].tail(12).min())
        stop = min(swing_low, close - 1.2 * a)
        risk = close - stop
        take_profit = close + risk * min_rr
    elif short_points >= 68:
        side = "SHORT"; points = short_points; reasons = short_reasons
        swing_high = float(d["high"].tail(12).max())
        stop = max(swing_high, close + 1.2 * a)
        risk = stop - close
        take_profit = close - risk * min_rr
    else:
        return None

    if risk <= 0:
        return None

    # Distance sanity: skip unrealistic moonshot targets versus recent 80-candle range.
    range80 = float(d["high"].tail(80).max() - d["low"].tail(80).min())
    if risk * min_rr > range80 * 1.35:
        return None

    probability = max(50, min(86, points + (3 if tp_mode == "TP2" else 0) - int((min_rr - 3) * 4)))
    return Signal(
        exchange=exchange,
        symbol=symbol,
        side=side,
        entry=round(close, 8),
        stop=round(float(stop), 8),
        take_profit=round(float(take_profit), 8),
        rr=float(min_rr),
        probability=float(probability),
        reason=", ".join(reasons),
    )
