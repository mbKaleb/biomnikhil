"""The biomnikhil app window. The launch scripts run it with the installer's
Python environment (it has pywebview) once biomnikhil is installed:

    python app/installer/window.py PORT

It starts the server (waitress, from runtime/.venv) in the background and shows
the app in its own window instead of a browser tab. Closing the window stops
the server. Only one window is open at a time: a new launch closes the old
windows (stopping the old server) and starts fresh, even when the old one is
on another port (the dev guest window, dev/guest_window.py, shares this).
The window also closes when data/.close-request appears: the Settings tab does
that to run setup or cleanup (app/services/maintenance.py).
"""
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import diagnostics

diagnostics.enable()

import webview

from core import APP, DATA, ROOT, VENV, WINDOWS
from gui import _mac_branding

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
URL = f"http://localhost:{PORT}"
LOG = DATA / "server.log"
CLOSE_REQUEST = DATA / ".close-request"
OPEN_WINDOW = DATA / ".open-window"  # the port of the window that's open

PAGE = """<!doctype html><html><body style="margin:0;height:100vh;display:flex;
align-items:center;justify-content:center;background:#0d1526;color:#cbd5e1;
font:15px -apple-system,'Segoe UI',sans-serif;text-align:center">
<div style="max-width:440px;line-height:1.5">{}</div></body></html>"""
STARTING = PAGE.format("Starting biomnikhil&hellip;")


def error_page(title, message):
    return PAGE.format(f'<div style="color:#fff;font-size:17px;margin-bottom:8px">{title}</div>{message}')


def running(port=PORT):
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/ping", timeout=2) as r:
            return b"biomnikhil" in r.read()
    except Exception:
        return False


def registered_port():
    """The port of the window that registered itself as open, if any."""
    try:
        return int(OPEN_WINDOW.read_text().strip())
    except (OSError, ValueError):
        return None


def unregister_window():
    if registered_port() == PORT:
        OPEN_WINDOW.unlink(missing_ok=True)


def port_taken():
    import socket
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def start_server():
    bin_dir = VENV / ("Scripts" if WINDOWS else "bin")
    env = dict(os.environ, PYTHONUTF8="1", VIRTUAL_ENV=str(VENV))
    # The agent can pip-install packages mid-run; this puts the venv's pip first.
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    log = open(LOG, "a", encoding="utf-8")
    # waitress, not `flask run --debug`: the debug server's autoreloader restarts
    # the whole process (killing any in-flight task) on any source edit,
    # including packages the agent pip-installs mid-run. --threads: each open
    # SSE trace stream pins a worker thread for the life of its task.
    return subprocess.Popen(
        [str(bin_dir / ("waitress-serve.exe" if WINDOWS else "waitress-serve")), f"--listen=127.0.0.1:{PORT}", "--threads=16", "app.wsgi:app"],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)


def stop(server):
    if server and server.poll() is None:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()


def wait_and_load(window, server):
    for _ in range(90):
        if running():
            window.load_url(URL)
            return
        if server and server.poll() is not None:
            break
        time.sleep(1)
    window.load_html(error_page(
        "biomnikhil didn't start",
        f"Close this window and open biomnikhil again. If it happens again, send "
        f"{LOG} to the lab's biomnikhil contact."))


def close_when_asked(window):
    while not CLOSE_REQUEST.exists():
        time.sleep(0.5)
    window.destroy()


def close_previous_windows():
    """Asks any window already open to close (each stops its own server on the
    way out), then waits for the old servers to stop answering. That covers a
    window on another port too, like the guest window next to the real app."""
    ports = {PORT}
    other_port = registered_port()
    if other_port:
        ports.add(other_port)

    def any_running():
        for port in ports:
            if running(port):
                return True
        return False

    if not any_running():
        CLOSE_REQUEST.unlink(missing_ok=True)
        return
    CLOSE_REQUEST.touch()
    for _ in range(40):
        if not any_running():
            break
        time.sleep(0.5)
    # Windows poll every half second; give them time to see the request.
    time.sleep(1.5)
    CLOSE_REQUEST.unlink(missing_ok=True)


def main():
    close_previous_windows()
    OPEN_WINDOW.write_text(str(PORT))
    server = None
    page = STARTING
    if not running():
        if port_taken():
            page = error_page(
                f"Port {PORT} is in use by another program",
                f"Close the other program that uses port {PORT}, then open biomnikhil again.")
        else:
            server = start_server()
    if sys.platform == "darwin":
        _mac_branding()
    # Downloads: the files the agent writes, offered from the chat.
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window("biomnikhil", html=page, width=1280, height=860,
                                   min_size=(800, 560), background_color="#0d1526")
    def closed():
        # On a Mac, closing the last window doesn't end the app, so
        # webview.start() wouldn't return: stop the server and quit here.
        stop(server)
        unregister_window()
        sys.stdout.flush()
        os._exit(0)

    window.events.closed += closed
    threading.Thread(target=close_when_asked, args=(window,), daemon=True).start()
    try:
        if page is STARTING:
            threading.Thread(target=wait_and_load, args=(window, server), daemon=True).start()
        # private_mode=False keeps cookies between launches, so the Cognito
        # sign-in page remembers you. storage_path is where Windows keeps them.
        webview.start(private_mode=False, storage_path=str(DATA / "webview"))
    except Exception:  # no window possible (no WebView2): fall back to the browser
        for _ in range(90):
            if running() or not server or server.poll() is not None:
                break
            time.sleep(1)
        webbrowser.open(URL)
        if server:
            server.wait()
    finally:
        stop(server)
        unregister_window()


if __name__ == "__main__":
    main()
    # pywebview can leave threads behind that would keep Python from exiting.
    sys.stdout.flush()
    os._exit(0)
