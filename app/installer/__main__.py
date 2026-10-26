"""biomnikhil installer. The launch scripts (app/run-mac.sh, app/run.ps1) run
it with a small Python environment that has pywebview:

    python app/installer [--console]

It shows the install window (ui/index.html), or falls back to plain text in
the console when no window can open (or with --console, or with
BIOMNIKHIL_NO_DIALOGS=1, which also skips the "press Enter" prompt).

Exit codes, read by the launch scripts: 0 = installed, start biomnikhil now;
3 = installed, don't start it; 1 = cancelled or failed.
"""
import os
import sys

from core import Installer, InstallFailed, welcome_info

START, INSTALLED, STOPPED = 0, 3, 1


def console(interactive):
    info = welcome_info()
    print(f"\nbiomnikhil {info['version']} setup")
    if info["blocked"]:
        b = info["blocked"]
        print(f"\n!! {b['title']}\n   {b['message']}")
        return STOPPED
    print(f"  Install location: {info['location']}")
    space = f"  ({info['free_gb']} GB free)" if info["free_gb"] is not None else ""
    print(f"  Space needed:     about {info['need_gb']} GB{space}")
    print(f"  Download now:     {info['download']}")
    print(f"  Python:           {info['python']}")
    print(f"  Shortcuts:        {info['shortcuts']}")
    if interactive:
        try:
            input("\nPress Enter to install, or close this window to cancel. ")
        except (EOFError, KeyboardInterrupt):
            return STOPPED
    print()
    tty = sys.stdout.isatty()
    last = {"status": None}

    def report(state):
        status = state["status"]
        if status == last["status"]:
            return
        if tty:
            print(f"\r\033[K  [{state['pct']:3d}%] {status}", end="", flush=True)
        elif status.split(" ", 2)[:2] != (last["status"] or "").split(" ", 2)[:2]:
            # Without a terminal, one line per phase rather than per package.
            print(f"  [{state['pct']:3d}%] {status}", flush=True)
        last["status"] = status

    try:
        result = Installer(report).run()
    except InstallFailed as e:
        print(f"\n\n!! {e.title}\n   {e.message}\n\n{e.details}\n")
        return STOPPED
    except KeyboardInterrupt:
        print("\n\nInstall stopped.")
        return STOPPED
    print("\n\nbiomnikhil is installed.")
    if result["note"]:
        print(result["note"])
    return START


def main():
    no_dialogs = bool(os.environ.get("BIOMNIKHIL_NO_DIALOGS"))
    if "--console" in sys.argv or no_dialogs:
        return console(interactive=not no_dialogs)
    try:
        import gui
        code = gui.run()
    except Exception as e:  # no pywebview, or no WebView2 on an older Windows
        if getattr(e, "window_shown", False):
            raise
        if not sys.stdin.isatty():  # no console to continue in (setup-mac.command closed it)
            print(f"The install window couldn't open ({e}).")
            if sys.platform == "darwin":
                import subprocess
                subprocess.run(["osascript", "-e", 'display alert "The biomnikhil setup window couldn\'t open." '
                                'message "Open Terminal, go to the biomnikhil folder and run: '
                                'BIOMNIKHIL_NO_DIALOGS=1 bash app/run-mac.sh" as critical'], capture_output=True)
            return STOPPED
        print(f"The install window couldn't open ({e}), so setup continues here.")
        return console(interactive=True)
    # pywebview can leave threads behind that would keep Python from exiting
    # once the window is gone.
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
