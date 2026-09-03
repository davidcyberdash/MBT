"""
OHLCV price fetching from MT5. Broker-agnostic — whatever terminal config.yaml
points at is the source of truth.
"""

from datetime import datetime, timedelta

from .connection import connect, mt5

# Timeframe names map to MT5's ENUM_TIMEFRAMES constants. The constants only
# exist when the (Windows-only) MetaTrader5 package imported; without it the
# keys stay valid so validation and error messages still read correctly, and
# every function here calls connect() first, which raises the clear
# "MetaTrader5 not importable" error before any value is used.
_TF_ATTRS = {
    "1m": "TIMEFRAME_M1",   "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15", "30m": "TIMEFRAME_M30",
    "1h": "TIMEFRAME_H1",   "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",   "1w": "TIMEFRAME_W1",
}
TIMEFRAME_MAP = {
    tf: (getattr(mt5, attr) if mt5 is not None else None)
    for tf, attr in _TF_ATTRS.items()
}

# Approx seconds per bar — used to bound the forward fetch window.
TF_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
}


def fetch_recent(symbol: str, timeframe: str, count: int = 100) -> list:
    """Most-recent `count` bars, newest first."""
    connect()
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Invalid timeframe '{timeframe}'. Options: {list(TIMEFRAME_MAP)}")

    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MAP[timeframe], 0, count)
    if rates is None:
        raise RuntimeError(f"No data for {symbol} {timeframe}: {mt5.last_error()}")

    return [
        {
            "time":   datetime.fromtimestamp(int(r["time"])).strftime("%Y-%m-%d %H:%M"),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": int(r["tick_volume"]),
        }
        for r in reversed(rates)
    ]


def get_cost_price(symbol: str, slippage_points: float = 5.0) -> float:
    """
    Per-trade transaction cost in PRICE terms, symbol-aware.
    Uses the broker's current spread (in points) + a slippage allowance, times
    the symbol's point size. Works correctly across forex, gold, indices, etc.
    """
    connect()
    si = mt5.symbol_info(symbol)
    if si is None:
        return 0.0
    return (si.spread + slippage_points) * si.point


def fetch_aligned(symbol: str, timeframe: str, signal_time: datetime,
                  entry_price: float, count: int = 1000, pre_hours: int = 12) -> list:
    """
    Return the tradeable bars for a signal, robust to timezone/DST offsets
    between the MQL5 log (broker server time) and the Python API (UTC).

    Strategy: fetch a window AROUND the signal time, find the bar whose close
    matches the logged entry price (the true signal bar), and return the bars
    strictly AFTER it. The first returned bar is the realistic entry bar — you
    enter at its OPEN, since the signal is only known at the prior bar's close.

    Returns [] if the signal bar can't be located (price never matches).
    """
    connect()
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Invalid timeframe '{timeframe}'.")

    pip   = 0.01 if symbol.upper().endswith("JPY") else 0.0001
    tol   = 0.5 * pip
    secs  = TF_SECONDS.get(timeframe, 3600)
    start = signal_time - timedelta(hours=pre_hours)
    end   = signal_time + timedelta(seconds=secs * count)

    rates = mt5.copy_rates_range(symbol, TIMEFRAME_MAP[timeframe], start, end)
    if rates is None or len(rates) == 0:
        return []

    # locate the signal bar: close matches entry, nearest in time to signal_time
    sig_ts = signal_time.timestamp()
    best_i, best_dist = None, None
    for i, r in enumerate(rates):
        if abs(float(r["close"]) - entry_price) <= tol:
            dist = abs(int(r["time"]) - sig_ts)
            if best_dist is None or dist < best_dist:
                best_dist, best_i = dist, i
    if best_i is None or best_i + 1 >= len(rates):
        return []

    # Shift all bar times into the signal's clock so the matched signal bar sits
    # exactly at signal_time. This makes every downstream time comparison
    # (sequential gating, exit times, equity) consistent regardless of the
    # broker-server vs UTC offset.
    matched_dt = datetime.fromtimestamp(int(rates[best_i]["time"]))
    offset = signal_time - matched_dt

    return [
        {
            "time":  datetime.fromtimestamp(int(r["time"])) + offset,
            "open":  float(r["open"]),
            "high":  float(r["high"]),
            "low":   float(r["low"]),
            "close": float(r["close"]),
        }
        for r in rates[best_i + 1:]
    ]


def fetch_after(symbol: str, timeframe: str, start: datetime, count: int = 1000) -> list:
    """
    Bars at or after `start`, chronological order (oldest first).
    Used by the backtester to replay each trade forward from its entry bar.

    Uses copy_rates_range (unambiguous: returns the [start, end] window) rather
    than copy_rates_from, which returns bars *ending* at the date.
    """
    connect()
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Invalid timeframe '{timeframe}'.")

    end = start + timedelta(seconds=TF_SECONDS.get(timeframe, 3600) * count)
    rates = mt5.copy_rates_range(symbol, TIMEFRAME_MAP[timeframe], start, end)
    if rates is None:
        return []

    return [
        {
            "time":  datetime.fromtimestamp(int(r["time"])),
            "open":  float(r["open"]),
            "high":  float(r["high"]),
            "low":   float(r["low"]),
            "close": float(r["close"]),
        }
        for r in rates
    ]
