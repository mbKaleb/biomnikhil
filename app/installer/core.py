"""The install itself, shared by the window (gui.py) and the console fallback.

Installer.run() goes through the steps below and reports through a callback;
anything that stops the install raises InstallFailed with plain-language text
for the Error screen. Every step is safe to repeat, so Retry just runs again:
finished work is skipped or comes from uv's cache in seconds.
"""
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

WINDOWS = sys.platform == "win32"
ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
DATA = ROOT / "data"
RUNTIME = ROOT / "runtime"
TOOLS = RUNTIME / "tools"
VENV = RUNTIME / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")
READY = VENV / ".biomnikhil-ready"  # the launch scripts skip setup once this exists
LOCK = APP / "requirements.lock"
LOG = DATA / "install.log"
UV = TOOLS / ("uv.exe" if WINDOWS else "uv")  # put there by the launch script
NEED_GB = 16  # packages and their download cache (~2 GB) plus the data lake (~14 GB)
PRIVATE_PYTHON = "3.12"

# (id, label, share of the progress bar)
STEPS = [
    ("check", "Checking this computer", 6),
    ("python", "Setting up Python", 12),
    ("packages", "Downloading packages", 76),
    ("settings", "Writing settings", 2),
    ("shortcuts", "Creating shortcuts", 4),
]


class InstallFailed(Exception):
    def __init__(self, title, message, details="", retry=True):
        super().__init__(title)
        self.title, self.message, self.details, self.retry = title, message, details, retry


def version():
    try:
        return (APP / "VERSION").read_text().strip()
    except OSError:
        return "dev"


def home_path(p):
    """~/... on a Mac, the full path on Windows, where ~ means nothing to people."""
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home):] if not WINDOWS and s.startswith(home + os.sep) else s


def free_gb():
    try:
        return shutil.disk_usage(ROOT).free // 1024**3
    except OSError:
        return None


def unsupported():
    """An InstallFailed for hardware or a folder biomnikhil can't run on, else None.
    Every package in requirements.lock is verified for Windows x64 and Apple
    Silicon on macOS 15+; pyopenms and biopython publish nothing else."""
    if WINDOWS:
        arch = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "")
        if arch.upper() != "AMD64":
            return InstallFailed(
                "This PC isn't supported",
                f"biomnikhil needs a 64-bit Intel or AMD PC. This one has an {arch or 'unknown'} "
                "processor, and some of biomnikhil's packages (biopython, pyopenms) aren't built for it.",
                retry=False)
        if re.search(r"\\OneDrive", str(ROOT), re.I):
            return InstallFailed(
                "Move biomnikhil out of OneDrive",
                "biomnikhil keeps about 16 GB in its folder, which OneDrive would try to sync. "
                "Move the biomnikhil folder somewhere else, for example C:\\biomnikhil, and run "
                "setup-windows.cmd from there.",
                details=f"folder: {ROOT}", retry=False)
    elif sys.platform == "darwin":
        if platform.machine() != "arm64":
            return InstallFailed(
                "This Mac isn't supported",
                "biomnikhil needs an Apple Silicon Mac (M1 or newer). One of its packages, "
                "pyopenms, isn't built for Intel Macs.", retry=False)
        mac = platform.mac_ver()[0]
        if mac and int(mac.split(".")[0]) < 15:
            return InstallFailed(
                "macOS needs an update",
                f"biomnikhil needs macOS 15 or newer, and this Mac has macOS {mac}. "
                "Update macOS in System Settings, then open setup-mac.command again.", retry=False)
    else:
        return InstallFailed("This computer isn't supported",
                             "biomnikhil installs on Windows PCs and Apple Silicon Macs.", retry=False)
    return None


def _probe(cmd):
    """Runs a candidate Python; returns (path, version) when it's 3.11-3.13."""
    code = ("import sys; print(sys.executable); print('%d.%d.%d' % sys.version_info[:3]); "
            "sys.exit(not (3, 11) <= sys.version_info[:2] <= (3, 13))")
    try:
        r = subprocess.run(cmd + ["-c", code], capture_output=True, text=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = r.stdout.split()
    return (lines[0], lines[1]) if r.returncode == 0 and len(lines) == 2 else None


def find_python():
    """An installed Python 3.11-3.13 as (path, version), or None."""
    if WINDOWS:
        cands = [["py", f"-3.{m}"] for m in (13, 12, 11)] + [["python"]]
    else:
        cands = [[n] for n in ("python3.13", "python3.12", "python3.11", "python3")]
    has_dev_tools = WINDOWS or subprocess.run(
        ["xcode-select", "-p"], capture_output=True).returncode == 0
    for cand in cands:
        exe = shutil.which(cand[0])
        if not exe:
            continue
        # Apple's /usr/bin/python3 pops up a developer-tools installer when those
        # tools are missing (and it's Python 3.9 anyway).
        if exe == "/usr/bin/python3" and not has_dev_tools:
            continue
        # The Microsoft Store's python.exe stub opens the Store instead of running.
        found = _probe([exe] + cand[1:])
        if found:
            return found
    return None


def private_python_ready():
    return any((RUNTIME / "python").glob(f"cpython-{PRIVATE_PYTHON}.*"))


def welcome_info():
    py = find_python()
    if py:
        download = "About 1 GB of packages"
        python = f"Uses your Python {py[1]}"
    else:
        download = "About 1 GB of packages" + ("" if private_python_ready() else ", plus Python (40 MB)")
        python = f"A private Python {PRIVATE_PYTHON}, kept in this folder"
    free = free_gb()
    blocked = unsupported()
    return {
        "version": version(),
        "location": home_path(ROOT),
        "free_gb": free,
        "need_gb": NEED_GB,
        "low_space": free is not None and free < NEED_GB,
        "download": download,
        "python": python,
        "shortcuts": "Desktop and Start menu" if WINDOWS else "Applications folder and Desktop",
        "blocked": error_dict(blocked) if blocked else None,
    }


def error_dict(e):
    return {"title": e.title, "message": e.message, "details": e.details, "retry": e.retry}


def required_hosts():
    """The sites biomnikhil needs: package and data downloads, sign-in, the model."""
    env = DATA / ".env" if (DATA / ".env").exists() else APP / ".env.example"
    cfg = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*)$", line)
        if m:
            cfg[m[1]] = m[2].strip()
    region = cfg.get("AWS_REGION") or "us-west-2"
    hosts = ["pypi.org", "files.pythonhosted.org", "github.com",
             "biomni-release.s3.amazonaws.com", f"bedrock-mantle.{region}.api.aws"]
    cognito = re.sub(r"^https?://", "", cfg.get("COGNITO_DOMAIN", "")).strip("/")
    if cognito:
        hosts.append(cognito)
    return hosts


def reachable(host):
    """True when the host answers a TLS handshake on 443. Certificates aren't
    checked: an answer is all this needs, and uv verifies them for real."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, 443), timeout=10) as s:
            with ctx.wrap_socket(s, server_hostname=host):
                return True
    except OSError:
        return False


# uv's trace output, which is the only place it reports each package:
_UNCACHED = re.compile(r"Identified uncached distribution: (\S+)")
_FETCHED = re.compile(r"Released lock at `(.*[\\/]wheels-v\d+[\\/].*\.lock)`")
_PREPARED = re.compile(r"^Prepared \d+ package")
_EXTRACTED = re.compile(r'Extracted wheel files name=PackageName\("([^"]+)"\)')
_NETWORK = re.compile(r"dns error|failed to lookup|connect|timed out|timeout|network|"
                      r"connection (reset|refused|closed)|tls|certificate", re.I)


class Installer:
    """report(state) gets a dict for the Installing screen after every change."""

    def __init__(self, report):
        self.report = report
        self.proc = None
        self.cancelled = threading.Event()
        self.made = []
        self.note = ""
        self.total = sum(1 for l in LOCK.read_text().splitlines() if re.match(r"[A-Za-z0-9]", l))

    # --- progress ---------------------------------------------------------------
    def _send(self, current, frac=0.0, status=None, sub=None):
        steps, pct, passed = [], 0.0, True
        for sid, label, weight in STEPS:
            if sid == current:
                passed = False
                state = "active"
                pct += weight * frac
            else:
                state = "done" if passed else "pending"
                pct += weight if passed else 0
            steps.append({"id": sid, "label": label, "state": state,
                          "sub": sub if sid == current else self.subs.get(sid)})
        if sub is not None:
            self.subs[current] = sub
        self.report({"steps": steps, "pct": round(min(pct, 100)),
                     "status": status or dict((s, l) for s, l, _ in STEPS)[current]})

    def _log(self, text):
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")

    def _env(self):
        env = dict(os.environ)
        env.update(UV_CACHE_DIR=str(TOOLS / "cache"),
                   UV_PYTHON_INSTALL_DIR=str(RUNTIME / "python"),
                   UV_NO_PROGRESS="1", UV_HTTP_TIMEOUT="120",
                   VIRTUAL_ENV="", NO_COLOR="1")
        return env

    def _uv(self, args, on_line=None):
        """Runs uv, logging its output; returns (exit code, recent lines)."""
        self._log(f"$ uv {' '.join(map(str, args))}")
        tail = []
        self.proc = subprocess.Popen(
            [str(UV)] + [str(a) for a in args], cwd=ROOT, env=self._env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
        with open(LOG, "a", encoding="utf-8") as log:
            for line in self.proc.stdout:
                if on_line:
                    on_line(line)
                if not line.startswith("TRACE"):
                    log.write(line)
                    if not line.startswith("DEBUG"):
                        tail = (tail + [line.rstrip()])[-12:]
        code = self.proc.wait()
        if self.cancelled.is_set():
            raise InstallFailed("Install stopped", "The install was cancelled.", retry=True)
        return code, tail

    def cancel(self):
        self.cancelled.set()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    # --- steps ------------------------------------------------------------------
    def run(self):
        self.subs = {}
        DATA.mkdir(exist_ok=True)
        RUNTIME.mkdir(exist_ok=True)
        LOG.write_text(f"biomnikhil {version()} install, {ROOT}\n", encoding="utf-8")
        try:
            self._check()
            self._python()
            self._packages()
            self._settings()
            self._shortcuts()
        except InstallFailed as e:
            self._log(f"!! {e.title}: {e.message}\n{e.details}")
            if not e.details:
                e.details = f"log: {home_path(LOG)}"
            else:
                e.details += f"\nlog: {home_path(LOG)}"
            raise
        READY.touch()
        self._log("done")
        return {"made": self.made, "note": self.note}

    def _check(self):
        self._send("check")
        problem = unsupported()
        if problem:
            raise problem
        if not WINDOWS:
            # Lets setup-mac.command open with a plain double-click from now on.
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(ROOT)], capture_output=True)
        self._send("check", 0.3, "Checking the internet connection")
        hosts = required_hosts()
        with ThreadPoolExecutor(len(hosts)) as pool:
            blocked = [h for h, ok in zip(hosts, pool.map(reachable, hosts)) if not ok]
        self._log("reachable: " + ", ".join(h for h in hosts if h not in blocked))
        if len(blocked) == len(hosts):
            raise InstallFailed(
                "Can't reach the internet",
                "Setup needs to download about 1 GB of packages. Check your Wi-Fi or VPN, "
                "then click Retry.", "unreachable: " + ", ".join(blocked))
        if blocked:
            raise InstallFailed(
                "Some sites are blocked on this network",
                "biomnikhil downloads its packages and data from these sites and sends model "
                "requests to AWS. Lab and hospital networks sometimes block them: try another "
                "network, or ask IT to allow them.", "unreachable: " + ", ".join(blocked))

    def _python(self):
        self._send("python")
        healthy = VENV_PY.exists() and subprocess.run(
            [str(VENV_PY), "-m", "pip", "--version"], capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0).returncode == 0
        if healthy and not os.environ.get("BIOMNIKHIL_REINSTALL"):
            ver = subprocess.run([str(VENV_PY), "-c", "import platform; print(platform.python_version())"],
                                 capture_output=True, text=True).stdout.strip()
            self._send("python", 1, f"Python {ver} ready", sub=ver)
            return
        py = find_python()
        if py:
            self._log(f"using {py[0]} ({py[1]})")
            self._send("python", 0.3, f"Creating the environment with Python {py[1]}")
            target = ["--python", py[0]]
        else:
            self._send("python", 0.1, "Downloading a private Python" if not private_python_ready()
                       else "Setting up the private Python")
            target = ["--managed-python", "--python", PRIVATE_PYTHON]
        # --seed: pip goes in too, for the packages the agent installs mid-run.
        code, tail = self._uv(["venv", "--clear", "--seed", *target, VENV])
        if code:
            net = any(_NETWORK.search(l) for l in tail)
            raise InstallFailed(
                "Couldn't set up Python",
                ("The download stopped. Check your internet connection, then click Retry."
                 if net else "Creating biomnikhil's Python environment failed. Click Retry; "
                 "if it fails again, send the details to the lab's biomnikhil contact."),
                "\n".join(tail))
        ver = subprocess.run([str(VENV_PY), "-c", "import platform; print(platform.python_version())"],
                             capture_output=True, text=True).stdout.strip()
        self._send("python", 1, f"Python {ver} ready", sub=ver)

    def _packages(self):
        total = self.total
        state = {"todo": None, "fetched": set(), "installed": set(), "phase": "download"}
        last = [None]

        def show():
            if state["phase"] == "download":
                todo = state["todo"] or 0
                n = min(total, total - todo + len(state["fetched"]))
                frac = 0.85 * n / total
                text = f"Downloading packages {n}/{total}"
            else:
                n = min(total, total - (state["todo"] or 0) + len(state["installed"]))
                frac = 0.85 + 0.15 * n / total
                text = f"Installing packages {n}/{total}"
            if (text, round(frac, 3)) != last[0]:
                last[0] = (text, round(frac, 3))
                self._send("packages", frac, text, f"{n} / {total}")

        def on_line(line):
            if m := _UNCACHED.search(line):
                state["todo"] = (state["todo"] or 0) + 1
            elif m := _FETCHED.search(line):
                state["fetched"].add(m[1])
            elif _PREPARED.search(line):
                state["phase"] = "install"
                state["todo"] = state["todo"] or 0
            elif m := _EXTRACTED.search(line):
                state["phase"] = "install"
                state["installed"].add(m[1])
            else:
                return
            show()

        self._send("packages", 0, f"Downloading packages 0/{total}", f"0 / {total}")
        code, tail = self._uv(["pip", "install", "-vv", "--python", VENV_PY, "-r", LOCK], on_line)
        if code:
            text = "\n".join(tail)
            if re.search(r"No space left|os error 28|disk full|not enough space", text, re.I):
                raise InstallFailed(
                    "The disk is full",
                    f"Free up some space (biomnikhil needs about {NEED_GB} GB), then click Retry. "
                    "Packages that already downloaded won't download again.", text)
            if _NETWORK.search(text):
                raise InstallFailed(
                    "The download was interrupted",
                    "Setup lost its connection while downloading packages. Check your Wi-Fi or VPN, "
                    "then click Retry. Packages that already downloaded won't download again.", text)
            raise InstallFailed(
                "Couldn't install the app's packages",
                "Click Retry; packages that already downloaded won't download again. If it "
                "fails again, send the details to the lab's biomnikhil contact.", text)
        self._send("packages", 1, f"Installed {total} packages", f"{total} / {total}")

    def _settings(self):
        self._send("settings")
        if not (DATA / ".env").exists():
            shutil.copyfile(APP / ".env.example", DATA / ".env")
            self._log("created data/.env")
        self.made.append("Settings file")

    def _shortcuts(self):
        self._send("shortcuts")
        try:
            if WINDOWS:
                _windows_shortcuts()
                self.made += ["Desktop", "Start menu"]
            else:
                _mac_app()
                self.made += ["Applications", "Desktop"]
        except Exception as e:  # biomnikhil works without them
            self._log(f"shortcuts failed: {e!r}")
            self.note = ("Couldn't add the shortcuts. Start biomnikhil with "
                         + ("setup-windows.cmd" if WINDOWS else "setup-mac.command")
                         + " in its folder instead.")
        self._send("shortcuts", 1)


INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>biomnikhil</string>
  <key>CFBundleIdentifier</key><string>edu.ucla.biomnikhil</string>
  <key>CFBundleExecutable</key><string>biomnikhil</string>
  <key>CFBundleIconFile</key><string>biomnikhil</string>
  <key>CFBundlePackageType</key><string>APPL</string>
</dict>
</plist>
"""


def _mac_app():
    """~/Applications/biomnikhil.app runs app/run-mac.sh without Terminal; a
    Desktop alias points at it. Built here rather than downloaded, so macOS
    doesn't quarantine it and it opens without an "Open Anyway" step."""
    import shlex
    app = Path.home() / "Applications" / "biomnikhil.app"
    shutil.rmtree(app, ignore_errors=True)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    shutil.copyfile(APP / "static" / "icons" / "biomnikhil.icns",
                    app / "Contents" / "Resources" / "biomnikhil.icns")
    (app / "Contents" / "Info.plist").write_text(INFO_PLIST)
    exe = app / "Contents" / "MacOS" / "biomnikhil"
    exe.write_text(f"#!/bin/bash\nexec /bin/bash {shlex.quote(str(APP / 'run-mac.sh'))}\n")
    exe.chmod(0o755)
    # Desktop alias, unless something real already has that name.
    alias = Path.home() / "Desktop" / "biomnikhil.app"
    if alias.is_symlink() or not alias.exists():
        alias.unlink(missing_ok=True)
        alias.symlink_to(app)
    # Shows the icon right away instead of after Finder's next refresh.
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/"
                    "LaunchServices.framework/Support/lsregister", "-f", str(app)], capture_output=True)


def _windows_shortcuts():
    """Desktop and Start menu shortcuts that run app\\run.ps1 in a hidden
    PowerShell, through the WScript.Shell object every Windows has."""
    def ps_quote(s):
        return "'" + str(s).replace("'", "''") + "'"
    script = f"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {{
    $lnk = $shell.CreateShortcut((Join-Path $dir 'biomnikhil.lnk'))
    $lnk.TargetPath = 'powershell.exe'
    $lnk.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + {ps_quote(APP / 'run.ps1')} + '"'
    $lnk.WindowStyle = 7  # minimized: no console flash before -WindowStyle Hidden applies
    $lnk.WorkingDirectory = {ps_quote(ROOT)}
    $lnk.IconLocation = {ps_quote(APP / 'static' / 'icons' / 'biomnikhil.ico')}
    $lnk.Description = 'Start biomnikhil'
    $lnk.Save()
}}
"""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-Command", script], capture_output=True, text=True, timeout=60,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"powershell exited with {r.returncode}")
