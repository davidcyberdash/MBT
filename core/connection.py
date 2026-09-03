"""
MT5 connection + config loading.

Everything is config-driven so the toolkit works for anyone: the user edits
config.yaml, never the code.
"""

import os
import yaml

# The MetaTrader5 package is Windows-only, and off-Windows it must run under the
# same Wine prefix's Python as the terminal. Import it optionally so the tools
# that DON'T need it (compile_ea, run_indicator, run_strategy_tester — they
# launch the terminal/MetaEditor as a subprocess) still work under a plain
# system Python, as the README describes. The data tools raise via require_mt5().
try:
    import MetaTrader5 as mt5
except ImportError:                      # pragma: no cover - platform dependent
    mt5 = None

MT5_UNAVAILABLE = (
    "The MetaTrader5 Python package is not importable, so live-data tools "
    "(get_ohlcv, get_signals with a relative path, backtest) are unavailable. "
    "It is Windows-only; off-Windows run this server under the Wine prefix's "
    "Python. The tester tools (compile_ea, run_indicator, run_strategy_tester) "
    "do not need it and work as-is."
)

_CONFIG_CACHE = None


def require_mt5():
    """Return the MetaTrader5 module, or raise a clear error if it is missing."""
    if mt5 is None:
        raise RuntimeError(MT5_UNAVAILABLE)
    return mt5


def _toolkit_root() -> str:
    """MBT folder (parent of core/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config() -> dict:
    """Load config.yaml from the MBT root. Cached after first read."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    path = os.path.join(_toolkit_root(), "config.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(
            "config.yaml not found. Copy config.example.yaml to config.yaml "
            "and set your MT5 path and signal file."
        )

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    _CONFIG_CACHE = cfg
    return cfg


def connect() -> bool:
    """
    Initialize the MT5 connection using the path in config.yaml.
    Idempotent — safe to call before every operation.
    """
    require_mt5()
    cfg  = load_config()
    path = (cfg.get("mt5_path") or "").strip()

    if path:
        ok = mt5.initialize(path=path)
    else:
        ok = mt5.initialize()

    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    return True


def signal_file_path() -> str:
    """
    Resolve the signal file location. If config gives an absolute path, use it.
    Otherwise treat it as a bare filename inside the MT5 .../MQL5/Files folder.
    """
    cfg  = load_config()
    name = cfg.get("signal_file", "signals.csv")

    if os.path.isabs(name):
        return name

    connect()
    data_path = mt5.terminal_info().data_path
    return os.path.join(data_path, "MQL5", "Files", name)


def reports_dir() -> str:
    cfg = load_config()
    d   = cfg.get("reports_dir", "reports")
    if not os.path.isabs(d):
        d = os.path.join(_toolkit_root(), d)
    os.makedirs(d, exist_ok=True)
    return d
