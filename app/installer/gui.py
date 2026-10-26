"""The install window: ui/index.html in a pywebview window. The page calls Api
methods through window.pywebview.api; progress goes back to it through
evaluate_js calls on window.ui."""
import json
import sys
import threading
from pathlib import Path

import webview

from core import Installer, InstallFailed, error_dict, welcome_info, APP

START, INSTALLED, STOPPED = 0, 3, 1
UI = Path(__file__).with_name("ui") / "index.html"


class Api:
    # pywebview walks this object's public attributes to build the JS API, so
    # everything that isn't a method for the page starts with an underscore.
    def __init__(self):
        self._window = None
        self._installer = None
        self._done = False
        self._code = STOPPED

    def _call(self, fn, arg):
        try:
            self._window.evaluate_js(f"ui.{fn}({json.dumps(arg)})")
        except Exception:  # the window closed mid-install
            pass

    def info(self):
        return welcome_info()

    def install(self):
        if self._installer and self._installer_thread.is_alive():
            return
        self._installer = Installer(lambda state: self._call("progress", state))
        self._installer_thread = threading.Thread(target=self._run, daemon=True)
        self._installer_thread.start()

    def _run(self):
        try:
            result = self._installer.run()
        except InstallFailed as e:
            self._call("error", error_dict(e))
        except Exception as e:  # a bug, but the page should still say something
            self._call("error", error_dict(InstallFailed(
                "Setup stopped unexpectedly",
                "Click Retry. If it happens again, send the details to the lab's biomnikhil contact.",
                f"{type(e).__name__}: {e}")))
        else:
            self._done = True
            self._code = INSTALLED
            self._call("done", result)

    def open_app(self):
        self._code = START
        self._destroy()

    def close(self):
        self._destroy()

    def _destroy(self):
        # Not from this thread: the page's call would wait on a window that's gone.
        threading.Thread(target=self._window.destroy, daemon=True).start()

    def _closing(self):
        if self._installer:
            self._installer.cancel()


def _mac_branding():
    """Names the app biomnikhil in the menu bar and Dock instead of Python."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "biomnikhil"
        icon = NSImage.alloc().initWithContentsOfFile_(str(APP / "static/icons/biomnikhil.icns"))
        NSApplication.sharedApplication().setApplicationIconImage_(icon)
    except Exception:
        pass


def run():
    api = Api()
    if sys.platform == "darwin":
        _mac_branding()
    window = webview.create_window(
        "biomnikhil Setup", str(UI), js_api=api, width=640, height=470,
        resizable=False, background_color="#0d1526", text_select=False)
    api._window = window
    window.events.closing += api._closing
    shown = threading.Event()
    window.events.shown += shown.set
    try:
        webview.start()
    except Exception as e:
        e.window_shown = shown.is_set()
        raise
    if not shown.is_set():
        raise RuntimeError("the window didn't appear")
    return api._code
