"""
MBT installer — one-shot setup.

Run from inside the MBT folder (Python 3.10+):
    python3 install.py

It will:
  1. install Python dependencies
  2. create config.yaml from the example if missing — off Windows it is
     pre-filled with the MT5 install found inside your Wine prefix
  3. copy SignalLogger.mqh into your MT5 MQL5/Include folder
  4. copy MBT_IndicatorHost.mq5 into MQL5/Experts (for headless run_indicator)
  5. off Windows, write the small Wine launcher shims the tools need
  6. print the exact `claude mcp add` command(s) to register the server

Why this file has two code paths
--------------------------------
There is no %APPDATA% on macOS (or Linux). MT5 is the MetaQuotes Wine build, so
the whole Windows tree lives inside a Wine prefix — on macOS typically

    ~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/...

and such installs are usually PORTABLE: MQL5/ sits beside terminal64.exe rather
than under AppData/Roaming/MetaQuotes/Terminal/<hash>/. There is also no `wine`
on PATH — the only Wine present is the one bundled inside the MT5 .app, and it
needs WINEPREFIX pointed at the right prefix, which is what the shims do.

Finally, the MetaTrader5 Python package is Windows-only (wheels only, no sdist),
so off Windows the tester tools run on the host Python while the data tools
(get_ohlcv / get_signals / backtest) need a second server on a Python installed
INSIDE the prefix. Step 6 prints how to set that up.
"""

import os
import re
import sys
import shutil
import stat
import subprocess
from glob import glob
from typing import NamedTuple

ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Where the shims go. Space-free on purpose: MBT builds the tester command with
# launcher.split(), so a launcher path containing spaces would be torn in two.
SHIM_DIR    = os.path.join(HOME, ".local", "bin")
SHIM_WINE   = os.path.join(SHIM_DIR, "mbt-wine")       # tester tools -> terminal/MetaEditor
SHIM_WINEPY = os.path.join(SHIM_DIR, "mbt-wine-py")    # data tools  -> Python in the prefix

# Where step 6 tells you to put a Windows Python inside the prefix.
WINE_PY_DIR = "mbt-py312"
WINE_PY_EXE = "C:\\%s\\python.exe" % WINE_PY_DIR
WINE_PY_URL = ("https://www.python.org/ftp/python/3.12.10/"
               "python-3.12.10-embed-amd64.zip")


class Install(NamedTuple):
    """One MT5 instance's MQL5 tree, plus what we know about how it launches."""
    root:     str          # .../MQL5
    terminal: str          # .../terminal64.exe ("" if not located)
    prefix:   str          # Wine prefix dir ("" on Windows)
    portable: bool         # data lives beside the exe -> launch with /portable


def step(msg):
    print(f"\n=== {msg} ===")


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def wine_prefixes():
    """Candidate Wine prefixes, most likely first.

    The official macOS MT5 wrapper installs to
    ~/Library/Application Support/net.metaquotes.wine.metatrader5, and brokers
    ship rebadged copies under their own bundle id — hence the wildcard rather
    than a hard-coded name.
    """
    pats = [
        os.path.join(HOME, "Library", "Application Support", "*", "drive_c"),
        os.path.join(HOME, "Library", "Application Support", "CrossOver",
                     "Bottles", "*", "drive_c"),
        os.path.join(HOME, ".wine", "drive_c"),
        os.path.join(HOME, ".local", "share", "wineprefixes", "*", "drive_c"),
        os.path.join(HOME, ".PlayOnLinux", "wineprefix", "*", "drive_c"),
    ]
    out = []
    for pat in pats:
        for hit in sorted(glob(pat)):
            pref = os.path.dirname(hit)
            if pref not in out:
                out.append(pref)
    return out


def terminals_in(prefix):
    """terminal64.exe files inside a prefix."""
    pats = [
        os.path.join(prefix, "drive_c", "Program Files", "*", "terminal64.exe"),
        os.path.join(prefix, "drive_c", "Program Files (x86)", "*", "terminal64.exe"),
        os.path.join(prefix, "drive_c", "*", "terminal64.exe"),
    ]
    out = []
    for pat in pats:
        for hit in sorted(glob(pat)):
            if hit not in out:
                out.append(hit)
    return out


def common_files_dir(prefix):
    """The shared Common/Files folder — where SignalLogger.mqh writes when the
    indicator runs under the Strategy Tester (FILE_COMMON), i.e. what
    run_indicator reads back."""
    hits = sorted(glob(os.path.join(prefix, "drive_c", "users", "*", "AppData",
                                    "Roaming", "MetaQuotes", "Terminal",
                                    "Common", "Files")))
    real = [h for h in hits if os.sep + "Public" + os.sep not in h]
    return (real or hits or [""])[0]


def looks_portable(inst_dir, prefix):
    """Does this install keep its data beside terminal64.exe?

    portable.txt is the documented marker, but the macOS MetaQuotes wrapper
    ships an install that behaves portably WITHOUT one: MQL5/ sits next to
    terminal64.exe and no AppData instance folder has an MQL5 tree of its own
    (the running terminal duly reports data_path = the install dir). Getting
    this wrong is quiet and nasty — the tester would launch without /portable,
    write its .set and report into the AppData instance instead, and the run
    would look like an indicator that logged nothing.
    """
    if os.path.isfile(os.path.join(inst_dir, "portable.txt")):
        return True
    if not os.path.isdir(os.path.join(inst_dir, "MQL5")):
        return False
    appdata_trees = glob(os.path.join(prefix, "drive_c", "users", "*", "AppData",
                                      "Roaming", "MetaQuotes", "Terminal", "*",
                                      "MQL5"))
    return not [t for t in appdata_trees if os.sep + "Common" + os.sep not in t]


def _windows_installs():
    base = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal")
    if not os.path.isdir(base):
        return []
    out = []
    for d in sorted(os.listdir(base)):
        root = os.path.join(base, d, "MQL5")
        if os.path.isdir(root):
            out.append(Install(root=root, terminal="", prefix="", portable=False))
    return out


def _wine_installs():
    """MQL5 trees inside every Wine prefix we can find.

    Two layouts, both checked: portable (MQL5 beside terminal64.exe) and the
    normal one (AppData/Roaming/MetaQuotes/Terminal/<hash>/MQL5).
    """
    out = []
    for prefix in wine_prefixes():
        terms = terminals_in(prefix)
        for term in terms:
            inst = os.path.dirname(term)
            beside = os.path.join(inst, "MQL5")
            if os.path.isdir(beside):
                out.append(Install(root=beside, terminal=term, prefix=prefix,
                                   portable=looks_portable(inst, prefix)))
        for hit in sorted(glob(os.path.join(prefix, "drive_c", "users", "*",
                                            "AppData", "Roaming", "MetaQuotes",
                                            "Terminal", "*", "MQL5"))):
            # Common/ is the shared folder, not a terminal instance.
            if os.sep + "Common" + os.sep in hit:
                continue
            out.append(Install(root=hit, terminal=(terms[0] if terms else ""),
                               prefix=prefix, portable=False))
    return out


def find_installs():
    return _windows_installs() if IS_WIN else _wine_installs()


def bundled_wine():
    """A wine binary. On macOS the MT5 .app ships its own; prefer that, because
    a Homebrew/upstream wine usually cannot read the wrapper's prefix."""
    pats = [
        "/Applications/*.app/Contents/SharedSupport/wine/bin/wine",
        os.path.join(HOME, "Applications", "*.app", "Contents", "SharedSupport",
                     "wine", "bin", "wine"),
    ]
    for pat in pats:
        for hit in sorted(glob(pat)):
            if os.access(hit, os.X_OK):
                return hit
    return shutil.which("wine") or ""


def to_win_path(path, prefix):
    """Windows-side spelling of a path inside a Wine prefix: drive_c is C:,
    everything else reaches the host filesystem through Wine's Z: mapping."""
    drive_c = os.path.join(prefix, "drive_c")
    if prefix and path.startswith(drive_c + os.sep):
        return "C:/" + path[len(drive_c) + 1:].replace(os.sep, "/")
    return "Z:/" + path.lstrip("/").replace(os.sep, "/")


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

def install_deps():
    step("Python dependencies")
    if sys.version_info < (3, 10):
        print("This is Python %d.%d — MBT needs 3.10+ (the mcp package does)."
              % sys.version_info[:2])
        print("Re-run with a newer interpreter:  python3 install.py")
        return
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if not in_venv and not IS_WIN:
        print("NOTE: not running in a virtualenv. On macOS the bare `python` is")
        print("      often an old system build, and Homebrew Pythons refuse to")
        print("      install into the system tree. If pip fails below:")
        print("          python3 -m venv .venv && .venv/bin/python install.py")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                             os.path.join(ROOT, "requirements.txt")])
    if result.returncode != 0:
        print("WARNING: pip install failed. Check the error above before continuing.")
    elif not IS_WIN:
        print("\nMetaTrader5 was skipped — it is Windows-only (no source dist).")
        print("The tester tools do not need it; see step 6 for the data tools.")


def _set_yaml_scalar(text, key, value):
    """Replace one `key: value` in place, keeping indentation and any trailing
    comment. Deliberately line-level: a PyYAML round-trip would strip the
    comments that make config.example.yaml worth reading."""
    pat = re.compile(r"^(?P<i>[ \t]*)%s:[ \t]*(?P<v>[^#\n]*?)[ \t]*(?P<c>#.*)?$"
                     % re.escape(key), re.M)

    def sub(m):
        tail = ("  " + m.group("c")) if m.group("c") else ""
        return "%s%s: %s%s" % (m.group("i"), key, value, tail)

    return pat.subn(sub, text, count=1)


def prefill_config(text, ins, launcher):
    """Fill the example config with a discovered Wine install's real paths.

    Two path styles, on purpose:
      * mt5_path      -> a WINDOWS path. It is only ever handed to
                         MetaTrader5.initialize(), a Windows DLL call.
      * tester.*      -> HOST paths. MBT does os.path work on these (locating
                         MQL5/, reading reports), so they must be paths this
                         filesystem can see; the shim converts where needed.
    """
    inst_dir = os.path.dirname(ins.terminal) if ins.terminal else ""
    data_dir = inst_dir if ins.portable else os.path.dirname(ins.root)
    common   = common_files_dir(ins.prefix)
    changed  = []

    values = [("signal_file", os.path.join(common, "signals.csv") if common else "signals.csv")]
    if ins.terminal:
        values.append(("mt5_path", to_win_path(ins.terminal, ins.prefix)))
        values.append(("terminal_path", ins.terminal))
        values.append(("metaeditor_path", os.path.join(inst_dir, "MetaEditor64.exe")))
    if data_dir:
        values.append(("data_dir", data_dir))
    if common:
        values.append(("common_files", common))
    if launcher:
        values.append(("launcher", launcher))

    for key, val in values:
        text, n = _set_yaml_scalar(text, key, '"%s"' % val)
        if n:
            changed.append(key)
    text, n = _set_yaml_scalar(text, "portable", "true" if ins.portable else "false")
    if n:
        changed.append("portable")
    return text, changed


def make_config(installs, launcher):
    step("Config")
    cfg = os.path.join(ROOT, "config.yaml")
    example = os.path.join(ROOT, "config.example.yaml")
    if os.path.exists(cfg):
        print("config.yaml already exists — leaving it untouched.")
        return

    text = open(example, encoding="utf-8").read()
    filled = []
    if not IS_WIN and installs:
        text, filled = prefill_config(text, installs[0], launcher)

    with open(cfg, "w", encoding="utf-8") as f:
        f.write(text)

    if filled:
        print("Created config.yaml, pre-filled from the MT5 install found at:")
        print("  %s" % (installs[0].terminal or installs[0].root))
        print("  keys set: %s" % ", ".join(filled))
        print("Check default_symbol / default_timeframe match your broker's names.")
    else:
        print("Created config.yaml from the example. EDIT IT: set mt5_path and signal_file.")


def copy_mql5_assets(installs):
    """SignalLogger.mqh -> MQL5/Include, MBT_IndicatorHost.mq5 -> MQL5/Experts."""
    step("MQL5 files")
    assets = [("SignalLogger.mqh", "Include"), ("MBT_IndicatorHost.mq5", "Experts")]
    if not installs:
        print("Could not find an MQL5 folder.")
        if not IS_WIN:
            print("Looked inside these Wine prefixes:")
            for p in wine_prefixes() or ["  (none found)"]:
                print("  %s" % p)
            print("If MT5 lives elsewhere, copy these by hand:")
        for name, sub in assets:
            print("  %s -> <your MQL5>/%s/" % (os.path.join(ROOT, "mql5", name), sub))
        return

    for ins in installs:
        print("%s%s" % (ins.root, "  (portable)" if ins.portable else ""))
        for name, sub in assets:
            dest_dir = os.path.join(ins.root, sub)
            try:
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy(os.path.join(ROOT, "mql5", name),
                            os.path.join(dest_dir, name))
                print("  copied %s -> %s/" % (name, sub))
            except OSError as e:
                print("  SKIPPED %s: %s" % (name, e))
    print("\nCompile the host EA once before the first headless run — ask Claude to")
    print("'compile the MBT indicator host', or open it in MetaEditor and press F7.")


def _write_shim(path, body):
    """Write an executable shim, reporting whether anything actually changed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path) and open(path, encoding="utf-8").read() == body:
        print("  %s (already current)" % path)
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("  wrote %s" % path)


def write_shims(installs):
    """The two launchers MBT needs off Windows.

    mbt-wine    runs terminal64.exe / MetaEditor64.exe — used by compile_ea,
                run_indicator and run_strategy_tester via tester.launcher.
    mbt-wine-py runs a Python installed inside the prefix, for the data tools.
                Its CWD is pinned to the MBT folder because Wine maps Z: to the
                filesystem root: with the CWD on Z:, the host absolute paths in
                config.yaml resolve unchanged from Windows Python, so one
                config.yaml serves both servers.
    """
    if IS_WIN:
        return ""
    step("Wine launcher shims")
    wine = bundled_wine()
    if not wine:
        print("No wine binary found (looked in /Applications/*.app and on PATH).")
        print("Install MT5's macOS build, or set tester.launcher in config.yaml.")
        return ""
    prefix = installs[0].prefix if installs else ""
    if not prefix:
        print("No Wine prefix found — skipping. Set tester.launcher by hand.")
        return ""

    print("  wine:   %s" % wine)
    print("  prefix: %s" % prefix)
    _write_shim(SHIM_WINE, (
        "#!/bin/sh\n"
        "# MBT launcher shim — runs MT5's terminal/MetaEditor under the right\n"
        "# Wine prefix. Named in config.yaml as tester.launcher.\n"
        "#\n"
        "# Why a script and not just \"wine\": there is usually no wine on PATH\n"
        "# (the only one is bundled inside the MT5 .app), it needs WINEPREFIX or\n"
        "# it would invent an empty ~/.wine, and MBT does launcher.split() so the\n"
        "# launcher cannot carry a path with spaces or an inline env assignment.\n"
        'export WINEPREFIX="%s"\n'
        'export WINEDEBUG="${WINEDEBUG:--all}"   # silence Wine fixme: chatter\n'
        'exec "%s" "$@"\n' % (prefix, wine)))
    _write_shim(SHIM_WINEPY, (
        "#!/bin/sh\n"
        "# MBT data-tool runner — the Python installed INSIDE the Wine prefix,\n"
        "# which is the only place the Windows-only MetaTrader5 package works.\n"
        "#\n"
        "# CWD is pinned to the MBT folder on purpose: Wine maps Z: to the real\n"
        "# filesystem root, so with the CWD on Z: the host absolute paths in\n"
        "# config.yaml resolve unchanged from Windows Python.\n"
        'cd "%s" || exit 1\n'
        'export WINEPREFIX="%s"\n'
        'export WINEDEBUG="${WINEDEBUG:--all}"\n'
        'exec "%s" "%s" "$@"\n' % (ROOT, prefix, wine, WINE_PY_EXE)))
    return SHIM_WINE


def print_mcp_cmd(installs):
    step("Register the MCP server with Claude Code")
    server = os.path.join(ROOT, "mcp_server.py").replace("\\", "/")
    py = sys.executable.replace("\\", "/")
    print("Run this once:\n")
    print('    claude mcp add mbt -s local -- "%s" "%s"\n' % (py, server))

    if not IS_WIN:
        prefix = installs[0].prefix if installs else "<your Wine prefix>"
        print("That server runs the tester tools (compile_ea, run_indicator,")
        print("run_strategy_tester). The data tools (get_ohlcv, get_signals,")
        print("backtest) need the Windows-only MetaTrader5 package, so they run")
        print("as a SECOND server on a Python inside the prefix. To set that up,")
        print("from this MBT folder:\n")
        print("    curl -LO %s" % WINE_PY_URL)
        print("    unzip -q python-3.12.10-embed-amd64.zip \\")
        print("        -d \"%s/drive_c/%s\"" % (prefix, WINE_PY_DIR))
        print("    # enable site-packages in the embeddable build, then pip:")
        print("    sed -i '' 's/^#import site/import site/' \\")
        print("        \"%s/drive_c/%s\"/python*._pth" % (prefix, WINE_PY_DIR))
        print("    curl -LO https://bootstrap.pypa.io/get-pip.py")
        print("    cp get-pip.py \"%s/drive_c/%s/\"" % (prefix, WINE_PY_DIR))
        # Quoted: unquoted C:\\...\\python.exe loses its backslashes to the shell.
        print('    %s "%s" "C:\\%s\\get-pip.py"' % (SHIM_WINE, WINE_PY_EXE, WINE_PY_DIR))
        print('    %s "%s" -m pip install -r requirements.txt' % (SHIM_WINE, WINE_PY_EXE))
        print("    claude mcp add mbt-data -s local -- %s mcp_server.py\n" % SHIM_WINEPY)
        print("(requirements.txt installs MetaTrader5 there, since that Python")
        print("really is Windows. The tester tools stay on the host server —")
        print("Windows Python cannot exec the shim.)\n")

    print("Then restart Claude Code. Verify with:  claude mcp list")


if __name__ == "__main__":
    from banner import banner
    sys.stdout.write(banner(stream=sys.stdout))
    installs = find_installs()
    install_deps()
    launcher = write_shims(installs)
    make_config(installs, launcher)
    copy_mql5_assets(installs)
    print_mcp_cmd(installs)
    print("\nDone. Edit config.yaml, then ask Claude to run your indicator "
          "(with SignalLogger) and backtest it — no chart needed.")
