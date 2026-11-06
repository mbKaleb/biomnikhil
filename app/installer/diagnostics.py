"""Records what the installer and app windows do to data/diagnostics.log, so a
failure on a machine we can't see can be read afterwards: Python errors, hard
crashes, pywebview's debug log, Windows Forms errors with their stack traces,
and the Windows and WebView2 versions. Call enable() before importing webview."""
import faulthandler
import os
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "data" / "diagnostics.log"
MAX_BYTES = 5_000_000
WEBVIEW2_KEYS = (
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
)


class _Tee:
    """Writes to the log and to the original stream, which pythonw leaves as None."""

    def __init__(self, log_file, original):
        self._log_file = log_file
        self._original = original

    def write(self, text):
        self._log_file.write(text)
        self._log_file.flush()
        if self._original is not None:
            self._original.write(text)
        return len(text)

    def flush(self):
        self._log_file.flush()
        if self._original is not None:
            self._original.flush()

    def isatty(self):
        return self._original is not None and self._original.isatty()

    def fileno(self):
        return self._log_file.fileno()


def _webview2_versions():
    import winreg
    found = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key_path in WEBVIEW2_KEYS:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    found.append(winreg.QueryValueEx(key, "pv")[0])
            except OSError:
                continue
    return found


def _log_system():
    print(f"\n===== {datetime.now().isoformat()} {sys.argv} =====")
    print(f"platform: {platform.platform()}")
    print(f"python: {sys.version} at {sys.executable}")
    print(f"cwd: {os.getcwd()}")
    print(f"root: {ROOT}")
    version_file = ROOT / "app" / "VERSION"
    if version_file.exists():
        print(f"version: {version_file.read_text().strip()}")
    if sys.platform == "win32":
        try:
            print(f"webview2: {_webview2_versions() or 'NOT FOUND'}")
        except Exception as e:
            print(f"webview2 check failed: {e!r}")


def _log_exception(kind, exc_type, exc, tb):
    print(f"\n!!! {kind}")
    traceback.print_exception(exc_type, exc, tb, file=sys.stdout)


def _hook_windows_forms():
    """Logs Windows Forms errors with their stack traces. Handling them also
    replaces the "Unhandled exception" dialog, which hides where they came from."""
    import clr
    clr.AddReference("System.Windows.Forms")
    from System import AppDomain
    from System.Windows.Forms import Application

    def on_thread_exception(_sender, args):
        print(f"\n!!! Windows Forms error\n{args.Exception.ToString()}", flush=True)
        print("".join(traceback.format_stack()), flush=True)

    def on_unhandled(_sender, args):
        print(f"\n!!! .NET unhandled error\n{args.ExceptionObject.ToString()}", flush=True)

    Application.ThreadException += on_thread_exception
    AppDomain.CurrentDomain.UnhandledException += on_unhandled


def enable():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > MAX_BYTES:
        LOG.unlink()
    log_file = open(LOG, "a", encoding="utf-8", errors="backslashreplace")
    sys.stdout = _Tee(log_file, sys.stdout)
    sys.stderr = _Tee(log_file, sys.stderr)
    faulthandler.enable(log_file)
    os.environ["PYWEBVIEW_LOG"] = "debug"

    def on_exception(exc_type, exc, tb):
        _log_exception("uncaught error", exc_type, exc, tb)

    def on_thread_error(args):
        _log_exception(f"uncaught error in thread {args.thread}", args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = on_exception
    threading.excepthook = on_thread_error
    _log_system()
    if sys.platform == "win32":
        try:
            _hook_windows_forms()
        except Exception as e:
            print(f"couldn't hook Windows Forms: {e!r}")
