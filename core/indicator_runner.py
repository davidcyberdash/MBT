"""
Headless indicator runner — make an indicator log its signals with no chart.

MT5 has no API to attach an indicator to a chart, and its Strategy Tester only
runs Experts. So MBT ships a tiny generic host EA (MBT_IndicatorHost) that loads
ANY indicator via iCustom inside the tester; once loaded the indicator's
OnCalculate runs bar-by-bar over the chosen date range, and if it logs its own
signals (via SignalLogger.mqh) those signals get written automatically.

This module drives that: it writes the host EA's .set file (which indicator to
load), clears any stale signal CSV, runs the tester headlessly, then reads back
the signals the indicator wrote to the shared Common\\Files folder.

Requirement: the indicator must log through SignalLogger.mqh — under the tester
that logger transparently writes to Common\\Files, which is where we read. An
indicator with a bespoke logger that writes only to the local Files folder lands
in the tester's per-agent sandbox and won't be found here.
"""

import os
import time
import subprocess

from .connection import reports_dir
from .tester import (_tester_cfg, _data_dir, _common_files_dir, _launch_cmd,
                     _run_name,
                     build_tester_ini, _fmt_date)

HOST_EA = "MBT_IndicatorHost"


def _profiles_tester_dir() -> str:
    """MT5 reads a tester .set file from MQL5/Profiles/Tester/ — return that dir
    (created if missing) under the active data dir."""
    d = os.path.join(_data_dir(), "MQL5", "Profiles", "Tester")
    os.makedirs(d, exist_ok=True)
    return d


def _write_set_file(name: str, indicator: str) -> str:
    """Write a minimal .set passing InpIndicator to the host EA. Returns the bare
    filename (ExpertParameters is resolved relative to Profiles/Tester)."""
    fname = name + ".set"
    path = os.path.join(_profiles_tester_dir(), fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write("InpIndicator=%s\n" % indicator)
        f.write("InpBuffersToPull=2\n")
    return fname


def _signal_csv_path(signal_file: str) -> str:
    """Where the hosted indicator's SignalLogger writes under the tester: the
    shared Common/Files folder, by the file's bare name."""
    return os.path.join(_common_files_dir(), os.path.basename(signal_file))


def _signal_span(path: str):
    """Return (count, first_time, last_time) over the signal CSV's data rows
    (excluding a header). Times are the raw first-column strings, or None."""
    if not os.path.isfile(path):
        return 0, None, None
    n = 0
    first = last = None
    with open(path, encoding="cp1252", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            if line.lower().startswith("time,"):
                continue                      # header (identified by content, not position)
            n += 1
            stamp = line.split(",", 1)[0].strip()
            if first is None:
                first = stamp
            last = stamp
    return n, first, last


def run_indicator(indicator, symbol, timeframe="h1", from_date=None, to_date=None,
                  signal_file="signals.csv", model=None, deposit=None,
                  leverage=None, timeout_sec=None, host_ea=HOST_EA) -> dict:
    """Run an indicator headlessly through the tester so it logs its own signals.

    indicator   : name under MQL5/Indicators (e.g. 'RegimePlusePro'), .ex5 optional.
    symbol      : broker symbol (e.g. 'XAUUSD').
    timeframe   : 1m 5m 15m 30m 1h 4h 1d 1w.
    from/to     : 'YYYY-MM-DD' (omit -> broker's full history).
    signal_file : the indicator's SignalLogFile name; read back from Common/Files.
                  Uses the indicator's compiled-in default, so leave as the default
                  unless the indicator hard-codes a different name.

    Returns the signal CSV path and the number of signals logged. The indicator
    runs with its DEFAULT inputs (iCustom uses compiled defaults) — if you tuned
    its inputs on a chart, recompile the indicator with those as defaults.
    """
    t = _tester_cfg()
    model    = model    or t.get("default_model", "open_prices")
    deposit  = deposit  or t.get("default_deposit", 10000)
    leverage = leverage or t.get("default_leverage", 100)
    timeout  = timeout_sec or t.get("timeout_sec", 1800)

    indicator = str(indicator).strip()
    if not indicator:
        return {"error": "indicator name is empty."}

    host_name = host_ea if host_ea.lower().endswith(".ex5") else host_ea + ".ex5"

    # The host EA is what loads the indicator; if it isn't compiled the tester runs
    # a non-existent Expert and logs nothing, which would look like an indicator
    # problem. Check up front and say precisely what to do, so a plain "backtest my
    # indicator" can self-recover (compile the host, then retry) without the user
    # needing to know the host EA exists.
    host_ex5 = os.path.join(_data_dir(), "MQL5", "Experts", host_name)
    if not os.path.isfile(host_ex5):
        return {"error": "The MBT host EA '%s' is not compiled (looked for %s). It's "
                        "what run_indicator uses to load your indicator headlessly — "
                        "compile it first (e.g. compile_ea '%s'); install.py puts its "
                        "source in MQL5/Experts." % (host_ea, host_ex5, host_ea)}

    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_name = _run_name(os.path.splitext(os.path.basename(indicator))[0], stamp)

    # .set tells the host EA which indicator to load.
    set_file = _write_set_file(run_name, indicator)

    ini_txt = build_tester_ini(host_name, symbol, timeframe, from_date, to_date,
                               model, deposit, leverage, report_path=run_name,
                               set_file=set_file)
    ini_path = os.path.join(reports_dir(), run_name + ".ini")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write(ini_txt)

    # STALE GUARD: clear the signal CSV before launch, so a leftover file from a
    # previous run can never be read back as this run's signals. Fail loud if it
    # can't be cleared (a silent stale read is exactly what this prevents).
    csv_path = _signal_csv_path(signal_file)
    if os.path.isfile(csv_path):
        try:
            os.remove(csv_path)
        except OSError:
            pass
        if os.path.isfile(csv_path):
            return {"error": "Could not clear the stale signal file at %s "
                            "(file locked?). Close any running MT5 terminal and "
                            "retry — leaving it risks reporting the previous run's "
                            "signals as this run's." % csv_path}

    cmd = _launch_cmd(ini_path)
    t0 = time.time()
    timed_out = False
    try:
        subprocess.run(cmd, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        timed_out = True
    elapsed = round(time.time() - t0, 1)

    count, first_sig, last_sig = _signal_span(csv_path)
    wrote = os.path.isfile(csv_path)

    result = {
        "indicator": indicator,
        "host_ea": host_ea,
        "symbol": symbol,
        "timeframe": timeframe,
        "from": _fmt_date(from_date),
        "to": _fmt_date(to_date),
        "model": model,
        "signal_csv": csv_path if wrote else None,
        "signal_count": count,
        "first_signal": first_sig,
        "last_signal": last_sig,
        "ran_seconds": elapsed,
        "timed_out": timed_out,
        "command": " ".join(cmd),
        "ini": ini_path,
        "set_file": set_file,
    }

    # The indicator computes over warm-up history the tester loads BEFORE
    # from_date, so it can log signals earlier than the requested start. Surface
    # that rather than let the wider span look like a bug.
    if count and from_date and first_sig and first_sig[:10].replace(".", "-") < _fmt_date(from_date).replace(".", "-"):
        result["note"] = ("signals start before from_date because the indicator also "
                          "computed over the tester's warm-up history; filter by date "
                          "downstream (e.g. backtest since_date) if you want only the "
                          "requested window.")

    if count == 0:
        # An instant return with nothing written usually means another terminal
        # already owns the data dir (MT5 is single-instance).
        if elapsed < 8 and not wrote:
            result["error"] = ("Terminal returned in %.1fs and wrote no signal file — "
                               "another MT5 terminal is almost certainly already "
                               "running on this data dir (single-instance). Close it "
                               "and retry." % elapsed)
        else:
            result["error"] = ("No signals were logged. Check that: the indicator "
                               "name is correct and compiled into MQL5/Indicators; "
                               "it logs via SignalLogger.mqh (bespoke loggers that "
                               "skip Common/Files land in the tester sandbox); the "
                               "symbol/timeframe has history for the date range; and "
                               "the strategy actually fires in that range.")
    return result
