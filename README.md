# MBT — MT5 Backtest Toolkit (MCP for Claude Code)

```
  ███╗   ███╗██████╗ ████████╗
  ████╗ ████║██╔══██╗╚══██╔══╝
  ██╔████╔██║██████╔╝   ██║
  ██║╚██╔╝██║██╔══██╗   ██║
  ██║ ╚═╝ ██║██████╔╝   ██║
  ╚═╝     ╚═╝╚═════╝    ╚═╝
   MT5 Backtest Toolkit · replay real signals on real bars
   ╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴
```

**Backtest, build and verify MetaTrader 5 strategies by talking to Claude AI — no scripting, no Python reimplementation, just real signals on real broker data.**

**Requirements:** Python 3.9+ · MetaTrader 5 (Windows, or Linux/macOS via Wine) · Claude Code

Drop one include into your indicator and MBT lets you, all through Claude:

- **Backtest your indicator's real signals** — replayed on real broker bars, with an HTML report.
- **Run your indicator headlessly** — no chart attach; Claude computes it over any date range and logs its signals for you.
- **Build, test & verify an Expert Advisor** — compile it, run MT5's real Strategy Tester on it, and prove it still matches the strategy.

The core principle: **MBT never recalculates your indicator in Python.** Your
indicator logs the signals *it* generated; MBT reads those real signals and
replays real broker price bars forward to see whether each trade hit its stop
or its target. What you backtest is exactly what your indicator drew.

---

## The problem we were solving

I use Claude to code my MQL5 indicators. To verify an indicator was calculating correctly, the natural next step was to have Claude write a Python script that checks the logic. The problem: if Claude made a mistake in the MQL5 code, there's a good chance it made the same mistake in the Python version it wrote to verify it. You're not getting an independent check — you're just confirming Claude was consistent with itself. Any bug in the original MQL5 code would still be invisible.

The solution was to make the indicator log its own decisions — every signal it fires, the exact entry, SL, TP, and context it calculated at that moment. Now you're not testing a copy. You're testing the real thing.

That's what MBT is built on. The indicator logs what it actually did. MBT reads those logs, fetches real broker price bars, and replays each trade forward — verifying the signals are generated correctly and whether the stop or the target was hit first. **What you backtest is exactly what your indicator drew — not a Python approximation of it.**

---

## How it works

```
  Your MT5 indicator                MBT (this toolkit)
  ┌──────────────────┐              ┌────────────────────────────┐
  │ #include          │   writes     │ reads signals.csv          │
  │  <SignalLogger>   ├─ signals.csv ┤                            │
  │ LogSignal(...)    │              │ replays real MT5 bars      │
  └──────────────────┘              │ forward → WIN / LOSS / OPEN │
                                     │                            │
                                     │ tools exposed to Claude:   │
                                     │  get_ohlcv                 │
                                     │  get_signals               │
                                     │  run_indicator (headless)  │
                                     │  backtest  (+ HTML report) │
                                     │  validate_signals          │
                                     └────────────────────────────┘
```

That's the indicator-backtest loop above. MBT also drives MT5's own toolchain to
**build, test and verify an Expert Advisor** (`compile_ea`, `run_strategy_tester`,
`signal_parity`) — see [Build, test & verify an Expert Advisor](#build-test--verify-an-expert-advisor).

---

## Install

> **New to this?** Paste the MBT repo URL into Claude Code and say:
> `"Clone this repo and set up the MBT MCP server for me"`
> Claude will clone it, run the installer, and register the server — just edit `config.yaml` when it's done.

```bash
cd MBT
python install.py
```

This installs dependencies, creates `config.yaml`, copies `SignalLogger.mqh`
into your MT5 `Include` folder and the headless host EA `MBT_IndicatorHost.mq5`
into `Experts`, and prints the command to register the MCP server with Claude Code.

Then edit **config.yaml**:

```yaml
mt5_path: "C:/.../terminal64.exe"   # the terminal your indicator runs on
signal_file: "signals.csv"           # must match SignalLogFile in your indicator
default_symbol: "EURUSD"
default_timeframe: "1h"
ambiguous_bar: "loss"                # conservative
```

> Running an indicator headlessly or testing an EA also needs the **`tester:`**
> block (terminal path, etc.) — copy it from `config.example.yaml` and fill it in.
> The plain indicator-replay backtest doesn't require it.

Register the server (printed by the installer):

```bash
claude mcp add mbt python "/abs/path/to/MBT/mcp_server.py"
```

**Using Claude Desktop instead of Claude Code?** Open
**Settings → Developer → Edit Config** (this opens `claude_desktop_config.json`)
and add:

```json
{
  "mcpServers": {
    "mbt": {
      "command": "python",
      "args": ["/abs/path/to/MBT/mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop to load it.

---

## Running on Linux / macOS (via Wine)

MetaTrader 5 and its Python package are Windows-only — but MBT runs fine on
Linux and macOS through [Wine](https://www.winehq.org/). The backtest results are
identical to Windows; only the setup differs.

**The one rule:** the `MetaTrader5` Python package talks to the terminal through a
Windows DLL, so it must run under the **same Wine prefix's Python** as MT5 — not
your system Python. System `python3` will always fail with
`ModuleNotFoundError: No module named 'MetaTrader5'`.

> This rule is only for the **replay engine** (`backtest`, `get_ohlcv`,
> `get_signals` — they read live data via the `MetaTrader5` package). The
> tester-based tools (`run_indicator`, `run_strategy_tester`, `compile_ea`) don't
> use that package at all — they launch the terminal/MetaEditor directly. On
> Linux/macOS they just need `launcher: "wine"` in the `tester:` block (the
> installer defaults it for you), and they run fine under system Python.

1. **Install MT5 under Wine** — download the installer from your broker (or
   MetaQuotes) and run it with Wine. It lands in a Wine prefix, e.g.
   `~/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe`.

2. **Install a Windows Python *into the same Wine prefix*** and add the deps:

   ```bash
   wine /path/to/wine/python.exe -m pip install MetaTrader5 numpy PyYAML mcp
   ```

3. **Point `config.yaml` at the Wine paths.** Use the Windows-style terminal path,
   and a `Z:`-mapped absolute path for the signal file (Wine maps `Z:` to `/`):

   ```yaml
   mt5_path: "C:/Program Files/MetaTrader 5/terminal64.exe"
   signal_file: "Z:/home/you/path/to/signals.csv"
   ```

4. **Run everything through the Wine Python**, not system Python:

   ```bash
   wine /path/to/wine/python.exe mcp_server.py        # the MCP server
   wine /path/to/wine/python.exe run_report.py        # a one-off backtest
   ```

> **Tip:** `SignalLogger.mqh` lives inside the Wine prefix's
> `MQL5/Include` folder — copy it there manually if `install.py` can't find it.
> Everything else (the Python core, the backtest engine, the HTML report) runs
> unmodified.

---

## Add logging to your indicator

```mql5
#include <SignalLogger.mqh>

// In OnInit (or on full recalculation) so the log matches the chart:
ResetSignalLog();

// When your buy/sell condition is true on bar `shift`:
LogSignal(shift, true,  entry, sl, tp, "TRENDING");  // BUY
LogSignal(shift, false, entry, sl, tp, "TRENDING");  // SELL
```

`regime` is optional — pass `""` if you don't use it. When present, the backtest
report breaks results down per regime.

Compile it — that's all. From here you have two ways to make the indicator log
its signals:

- **Headless (recommended):** ask Claude to run it — `run_indicator` computes the
  indicator over your chosen date range with no chart and writes the signals
  automatically. Nothing to attach.
- **On a chart:** attach the compiled indicator to a chart as usual; it writes
  `signals.csv` live as it runs.

Either way you end up with the same signal file, which the `backtest` tool reads.

---

## Use it

Just describe what you want in Claude Code — Claude picks the right tool automatically.

**Run your indicator and backtest it — no chart needed:**

> "run my RegimePlusePro indicator on XAUUSD H1 for the last 3 years, then backtest it"

Claude runs the indicator headlessly (it computes over the whole range and logs
its own signals), then replays those signals on real bars and hands you the
metrics + an HTML report. You never open a chart. (First time, Claude will sort
out any one-time setup for you.)

**Or backtest signals you already have** (from a previous run, or an indicator
running live on a chart):

> "backtest my signals"
> "backtest signals since 2026-01-01 and give me the HTML report"
> "validate my signal file"
> "fetch the last 300 EURUSD 1h bars"

The `backtest` tool returns full metrics and writes an HTML report (with an
equity curve) to `reports/`.

### Build, test & verify an Expert Advisor

Once you have an EA (or ask Claude to build one from your indicator), MBT lets
you compile it, run it through MT5's real tester, and confirm it trades exactly
the same way your indicator does — all by talking to Claude:

**Step 1 — compile and fix:**
> "check my EA for errors"
> "fix the errors and compile again"

Claude will compile with MetaEditor, read the structured errors back, fix the
code, and recompile until it's clean — without you needing to open MetaEditor.

**Step 2 — backtest the real EA code:**
> "run a strategy tester backtest of my EA on XAUUSD H1 from 2018 to now"

This runs MT5's own headless Strategy Tester — real spread, swaps and execution
— not a Python simulation. Returns the same metrics you'd see in MT5's Strategy
Tester report.

**Step 3 — cross-check EA results against your indicator:**
> "compare my EA's results with my indicator backtest — do they match?"

If you already ran a signal-replay backtest of your indicator, this checks
whether the EA produces the same trades on the same bars. The first bar where
they diverge is pinpointed so you know exactly where the port drifted.

### Setup for the headless run & EA tools

The plain-English prompts above are all you need day to day — this is the one-time
plumbing behind them (Claude can do all of it for you).

- **`tester:` block** — both the headless `run_indicator` and the EA tools
  (`compile_ea`, `run_strategy_tester`, `signal_parity`) need it filled in; copy it
  from `config.example.yaml`. The plain indicator-replay `backtest` does not.
- **Host EA** — `run_indicator` loads your indicator through a bundled helper EA,
  `MBT_IndicatorHost.mq5`. `install.py` copies it into `MQL5/Experts`; it must be
  compiled once (just ask Claude to "compile the MBT indicator host").
- **Indicator logging** — the indicator must log via `SignalLogger.mqh` (a custom
  logger that writes only to the terminal's local `Files` folder isn't visible to
  the headless runner). Headless runs use the indicator's **default** inputs.
- **Linux/macOS** — these tools launch the terminal directly through Wine; set
  `launcher: "wine"` (the installer defaults it) and `portable: true` if the install
  keeps its data beside the exe.

---

## What the report contains

- Total trades · wins · losses · open
- Win rate · profit factor · expectancy (avg R)
- Net result · max drawdown (in R)
- Average win / loss · max win/loss streaks
- Per-regime breakdown
- Equity curve (cumulative R)

Everything is in **R units** (1R = the risk on each trade, entry→SL), so
results compare cleanly across symbols, timeframes, and account sizes.

---

## Signal CSV format

`SignalLogger.mqh` writes this standard header:

```
time,symbol,timeframe,direction,entry,sl,tp,regime
```

Any file matching this format works — MBT is indicator-agnostic. Extra columns
are ignored, and a header-less legacy file is still read if its first column is
a timestamp.

---

## Tools (MCP)

| Tool | Purpose |
|------|---------|
| `ping` | check MT5 is running and reachable |
| `get_ohlcv` | live OHLCV bars for any symbol/timeframe (max 2000) |
| `get_signals` | read your indicator's logged signals |
| `backtest` | replay + full metrics + HTML report (requires internet for chart) |
| `validate_signals` | check SL/TP geometry of every signal |
| `get_config` | show the active terminal + signal file |
| `compile_ea` | compile an EA/indicator with MetaEditor → structured errors/warnings |
| `run_indicator` | run an indicator headlessly so it logs its signals — no chart attach |
| `run_strategy_tester` | run MT5's real headless Strategy Tester on an EA → metrics + report |
| `signal_parity` | diff two signal sets and report the first divergence |

> **Note:** the HTML report is written to `reports/` on your machine and opened in a
> browser. Claude cannot open it directly — it will give you the file path.

---

## Examples

`examples/` contains sample signal CSVs you can use to try MBT without attaching
an indicator. Point `signal_file` in `config.yaml` to any of them:

```yaml
signal_file: "C:/abs/path/to/MBT/examples/test_signals.csv"
```

---

## Research scripts

`scripts/` contains one-off analysis scripts used during strategy development.
They are not part of the core toolkit and have no documentation — treat them as
reference material, not user tools.

---

## Built on YouTube

This toolkit was built live on [FX David](https://www.youtube.com/@fxdavid9392) — a series on building, verifying, and backtesting MT5 indicators with Claude AI.
