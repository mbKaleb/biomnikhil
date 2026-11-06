"""Self-update for release installs, run by the launch scripts before the app
starts (app/run-mac.sh, app/run.ps1):

    python app/installer/update.py

Every release carries release.txt (the version and the zip's SHA-256), signed
in the release workflow with the key whose public half is PUBLIC_KEY below.
An update goes ahead only when that signature checks out, the release is newer
than this install, and the downloaded zip has the signed SHA-256. Then its
files replace this install's (each file swapped in whole, app/VERSION last, so
an interrupted update runs again on the next start), and files a release
removed are deleted from app/. data/ and runtime/ aren't touched.

Standard library only: it runs with whichever Python the install has.

Exit codes, read by the launch scripts: 0 = updated, restart on the new
version; 2 = already up to date; 1 = didn't update (offline, unsigned or bad
release), start the current version.
"""
import base64
import hashlib
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = ROOT / "app" / "VERSION"
RELEASES = "https://github.com/mbKaleb/biomnikhil/releases/latest/download"

# Ed25519 public key that signs releases (raw 32 bytes, hex). The private key
# is the RELEASE_SIGNING_KEY secret of the GitHub repo. To rotate it, ship a
# release signed with the old key whose PUBLIC_KEY is the new one.
PUBLIC_KEY = bytes.fromhex("ad02842f480aac0662ec7800c4fa7aa3ee09dc4ce989246a8c508debd1148d22")

UPDATED, UP_TO_DATE, NOT_UPDATED = 0, 2, 1


class UpdateRefused(Exception):
    pass


# ---- Ed25519 signature check (RFC 8032, section 5.1.7) ------------------------
# Verification only, so there's no secret to leak through timing. Names follow
# the RFC: P is the field prime, L the group order, D the curve constant.
# Points are extended coordinates (X, Y, Z, T).

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_MINUS_ONE = pow(2, (P - 1) // 4, P)


def _point_add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    e1 = (y1 - x1) * (y2 - x2) % P
    e2 = (y1 + x1) * (y2 + x2) % P
    e3 = 2 * t1 * t2 * D % P
    e4 = 2 * z1 * z2 % P
    e = e2 - e1
    f = e4 - e3
    g = e4 + e3
    h = e2 + e1
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _point_multiply(scalar, point):
    result = (0, 1, 1, 0)  # the neutral element
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _points_equal(a, b):
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    same_x = (x1 * z2 - x2 * z1) % P == 0
    same_y = (y1 * z2 - y2 * z1) % P == 0
    return same_x and same_y


def _recover_x(y, sign):
    if y >= P:
        return None
    x_squared = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x_squared == 0:
        return None if sign else 0
    x = pow(x_squared, (P + 3) // 8, P)
    if (x * x - x_squared) % P != 0:
        x = x * SQRT_MINUS_ONE % P
    if (x * x - x_squared) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _decompress(encoded):
    if len(encoded) != 32:
        return None
    y = int.from_bytes(encoded, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


_BASE_Y = 4 * pow(5, P - 2, P) % P
_BASE_X = _recover_x(_BASE_Y, 0)
BASE_POINT = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % P)


def verify_signature(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True when signature is public_key's Ed25519 signature of message."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    digest = hashlib.sha512(signature[:32] + public_key + message).digest()
    h = int.from_bytes(digest, "little") % L
    left = _point_multiply(s, BASE_POINT)
    right = _point_add(r, _point_multiply(h, a))
    return _points_equal(left, right)


# ---- the signed release statement ---------------------------------------------

_STATEMENT = re.compile(
    r"biomnikhil release\n"
    # Test builds (0.2.0-rc1) are signed too, though is_newer never takes one.
    r"version: (?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)\n"
    r"sha256: (?P<sha256>[0-9a-f]{64})\n"
)


def parse_statement(statement: bytes, signature_b64: bytes, public_key: bytes | None = None) -> dict:
    """{"version", "sha256"} from release.txt, once its signature checks out
    (against PUBLIC_KEY unless another key is given)."""
    if public_key is None:
        public_key = PUBLIC_KEY
    try:
        signature = base64.b64decode(signature_b64.strip(), validate=True)
    except ValueError:
        raise UpdateRefused("the release signature isn't valid base64")
    if not verify_signature(public_key, statement, signature):
        raise UpdateRefused("the release signature doesn't match biomnikhil's release key")
    match = _STATEMENT.fullmatch(statement.decode("utf-8", errors="replace"))
    if match is None:
        raise UpdateRefused("release.txt isn't in the expected format")
    return {"version": match["version"], "sha256": match["sha256"]}


def version_tuple(version: str) -> tuple[int, ...] | None:
    """(0, 3, 1) for "0.3.1"; None for anything else (test builds, "dev")."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return None
    parts = []
    for part in version.split("."):
        parts.append(int(part))
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    candidate_parts = version_tuple(candidate)
    current_parts = version_tuple(current)
    if candidate_parts is None or current_parts is None:
        return False
    return candidate_parts > current_parts


# ---- unpacking and swapping in ------------------------------------------------

def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract(zip_path: Path, destination: Path) -> Path:
    """Unpacks the release zip, keeping the executable bits (zipfile alone
    drops them, and setup-mac.command needs them). Returns the biomnikhil/
    folder inside it."""
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if not name.startswith("biomnikhil/") or ".." in Path(name).parts:
                raise UpdateRefused(f"unexpected path in the release zip: {name}")
            extracted = Path(archive.extract(info, destination))
            mode = (info.external_attr >> 16) & 0o777
            if mode and not info.is_dir() and os.name != "nt":
                extracted.chmod(mode)
    return destination / "biomnikhil"


def _files_under(folder: Path) -> set[str]:
    """Paths of every file under folder, relative and with forward slashes."""
    found = set()
    for path in folder.rglob("*"):
        if path.is_file():
            found.add(path.relative_to(folder).as_posix())
    return found


def _replace_file(source: Path, destination: Path) -> None:
    """Copies source over destination in one step: a partly written file never
    sits at destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.update")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


# Files older releases put at the top of the install. Only the setup launchers
# belong there now.
RETIRED_ROOT_FILES = ("run-mac.sh", "run.ps1", "LICENSE", "agent-ideas.md", "shared-data-lake.md")


def apply(release_root: Path, install_root: Path) -> None:
    """Makes install_root's files match release_root's: copies every release
    file in, then deletes files under app/ the release doesn't have. app/VERSION
    goes last, so until it does, the next start sees an update still to do."""
    release_files = _files_under(release_root)
    if "app/VERSION" not in release_files:
        raise UpdateRefused("the release zip has no app/VERSION")

    for relative in sorted(release_files):
        if relative == "app/VERSION":
            continue
        _replace_file(release_root / relative, install_root / relative)

    app_dir = install_root / "app"
    for relative in sorted(_files_under(app_dir)):
        full = f"app/{relative}"
        if full in release_files:
            continue
        if "__pycache__" in relative.split("/"):
            continue
        (app_dir / relative).unlink()

    for name in RETIRED_ROOT_FILES:
        if name not in release_files:
            (install_root / name).unlink(missing_ok=True)

    # Folders the deletions emptied, deepest first.
    folders = [p for p in app_dir.rglob("*") if p.is_dir()]
    folders.sort(key=lambda p: len(p.parts), reverse=True)
    for folder in folders:
        if not any(folder.iterdir()):
            folder.rmdir()

    _replace_file(release_root / "app" / "VERSION", install_root / "app" / "VERSION")


# ---- the update ---------------------------------------------------------------

def _download(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "biomnikhil-updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def update(install_root: Path = ROOT, releases: str = RELEASES) -> int:
    # A git checkout updates with git; applying a release would delete every
    # untracked file under app/.
    if (install_root / ".git").exists():
        return UP_TO_DATE
    current = (install_root / "app" / "VERSION").read_text().strip()
    try:
        statement = _download(f"{releases}/release.txt", timeout=10)
        signature = _download(f"{releases}/release.txt.sig", timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(">> The latest release isn't signed, so biomnikhil won't update to it.")
        else:
            print(f">> Couldn't check for an update (HTTP {exc.code}).")
        return NOT_UPDATED
    except OSError as exc:
        print(f">> Couldn't check for an update ({exc}).")
        return NOT_UPDATED

    try:
        release = parse_statement(statement, signature)
    except UpdateRefused as exc:
        print(f">> Not updating: {exc}.")
        return NOT_UPDATED
    if not is_newer(release["version"], current):
        return UP_TO_DATE

    print(f">> Updating biomnikhil {current} -> {release['version']}")
    with tempfile.TemporaryDirectory(prefix="biomnikhil-update-") as tmp:
        tmp_dir = Path(tmp)
        zip_path = tmp_dir / "biomnikhil.zip"
        try:
            zip_path.write_bytes(_download(f"{releases}/biomnikhil.zip", timeout=300))
        except OSError as exc:
            print(f">> Update download failed ({exc}), starting {current}.")
            return NOT_UPDATED
        try:
            if sha256_of(zip_path) != release["sha256"]:
                raise UpdateRefused("the downloaded zip doesn't match the signed release")
            release_root = extract(zip_path, tmp_dir / "unpacked")
            zipped_version = (release_root / "app" / "VERSION").read_text().strip()
            if zipped_version != release["version"]:
                raise UpdateRefused(f"the zip is version {zipped_version}, not {release['version']}")
            apply(release_root, install_root)
        except (UpdateRefused, OSError, zipfile.BadZipFile) as exc:
            print(f">> Not updating: {exc}. Starting {current}.")
            return NOT_UPDATED
    return UPDATED


if __name__ == "__main__":
    sys.exit(update())
