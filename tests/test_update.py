"""The signed self-update (app/installer/update.py)."""
import base64
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import update

# RFC 8032, section 7.1, tests 1 and 2.
RFC_VECTORS = [
    (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
        "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da08"
        "5ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


@pytest.mark.parametrize("public_hex, message_hex, signature_hex", RFC_VECTORS)
def test_rfc_8032_vectors_verify(public_hex, message_hex, signature_hex):
    public = bytes.fromhex(public_hex)
    message = bytes.fromhex(message_hex)
    signature = bytes.fromhex(signature_hex)
    assert update.verify_signature(public, message, signature)


@pytest.mark.parametrize("public_hex, message_hex, signature_hex", RFC_VECTORS)
def test_changed_message_fails(public_hex, message_hex, signature_hex):
    public = bytes.fromhex(public_hex)
    message = bytes.fromhex(message_hex) + b"!"
    signature = bytes.fromhex(signature_hex)
    assert not update.verify_signature(public, message, signature)


def test_malformed_inputs_fail():
    public = bytes.fromhex(RFC_VECTORS[0][0])
    signature = bytes.fromhex(RFC_VECTORS[0][2])
    assert not update.verify_signature(public[:31], b"", signature)
    assert not update.verify_signature(public, b"", signature[:63])
    # s >= L: the malleability check.
    big_s = signature[:32] + (2**256 - 1).to_bytes(32, "little")
    assert not update.verify_signature(public, b"", big_s)


@pytest.mark.parametrize("candidate, current, newer", [
    ("0.3.0", "0.2.9", True),
    ("0.10.0", "0.9.0", True),
    ("0.2.0", "0.2.0", False),
    ("0.1.0", "0.2.0", False),  # never downgrades
    ("0.3.0-rc1", "0.2.0", False),  # never takes a test build
    ("0.3.0", "dev", False),
])
def test_is_newer(candidate, current, newer):
    assert update.is_newer(candidate, current) is newer


# ---- signing with openssl, as the release workflow does -----------------------

openssl_missing = shutil.which("openssl") is None


@pytest.fixture
def signing_key(tmp_path):
    """A fresh Ed25519 key: (private key path, raw public key)."""
    if openssl_missing:
        pytest.skip("openssl isn't installed")
    key = tmp_path / "key.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key)], check=True)
    der = subprocess.run(
        ["openssl", "pkey", "-in", str(key), "-pubout", "-outform", "DER"],
        check=True, capture_output=True,
    ).stdout
    return key, der[-32:]


def _sign(key: Path, message: bytes, tmp_path: Path) -> bytes:
    message_file = tmp_path / "message"
    message_file.write_bytes(message)
    signature = subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(message_file)],
        check=True, capture_output=True,
    ).stdout
    return base64.b64encode(signature)


def _statement(version: str, sha256: str) -> bytes:
    return f"biomnikhil release\nversion: {version}\nsha256: {sha256}\n".encode()


def test_openssl_signature_is_accepted(signing_key, tmp_path):
    key, public = signing_key
    statement = _statement("1.2.3", "ab" * 32)
    release = update.parse_statement(statement, _sign(key, statement, tmp_path), public)
    assert release == {"version": "1.2.3", "sha256": "ab" * 32}


def test_tampered_statement_is_refused(signing_key, tmp_path):
    key, public = signing_key
    statement = _statement("1.2.3", "ab" * 32)
    signature = _sign(key, statement, tmp_path)
    with pytest.raises(update.UpdateRefused):
        update.parse_statement(statement.replace(b"1.2.3", b"9.9.9"), signature, public)


def test_other_key_is_refused(signing_key, tmp_path):
    key, _public = signing_key
    statement = _statement("1.2.3", "ab" * 32)
    with pytest.raises(update.UpdateRefused):
        update.parse_statement(statement, _sign(key, statement, tmp_path), update.PUBLIC_KEY)


# ---- the whole update, against a release folder on disk -----------------------

def _make_release_zip(path: Path, version: str, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in {**files, "app/VERSION": f"{version}\n".encode()}.items():
            info = zipfile.ZipInfo(f"biomnikhil/{name}")
            mode = 0o755 if name.endswith((".sh", ".command")) else 0o644
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, data)


def _make_install(root: Path, version: str) -> None:
    (root / "app" / "old_module").mkdir(parents=True)
    (root / "app" / "VERSION").write_text(f"{version}\n")
    (root / "app" / "routes.py").write_text("old")
    (root / "app" / "old_module" / "gone.py").write_text("removed in the new release")
    (root / "app" / "__pycache__").mkdir()
    (root / "app" / "__pycache__" / "routes.cpython-312.pyc").write_bytes(b"")
    (root / "data").mkdir()
    (root / "data" / ".env").write_text("PORT=8000\n")
    (root / "runtime").mkdir()
    (root / "runtime" / "marker").write_text("kept")


@pytest.fixture
def release(signing_key, tmp_path, monkeypatch):
    """A signed 0.3.0 release in a folder, served as file:// URLs, and an
    install of 0.2.0 to update."""
    key, public = signing_key
    monkeypatch.setattr(update, "PUBLIC_KEY", public)
    releases = tmp_path / "releases"
    releases.mkdir()
    zip_path = releases / "biomnikhil.zip"
    _make_release_zip(zip_path, "0.3.0", {
        "app/routes.py": b"new",
        "app/run-mac.sh": b"#!/bin/bash\n",
        "setup-mac.command": b"#!/bin/bash\n",
    })
    statement = _statement("0.3.0", update.sha256_of(zip_path))
    (releases / "release.txt").write_bytes(statement)
    (releases / "release.txt.sig").write_bytes(_sign(key, statement, tmp_path))
    install = tmp_path / "install"
    _make_install(install, "0.2.0")
    return {"url": releases.as_uri(), "dir": releases, "install": install}


def test_update_applies_a_signed_release(release):
    install = release["install"]
    assert update.update(install, release["url"]) == update.UPDATED
    assert (install / "app" / "VERSION").read_text().strip() == "0.3.0"
    assert (install / "app" / "routes.py").read_text() == "new"
    assert not (install / "app" / "old_module").exists()  # removed with its file
    assert (install / "data" / ".env").read_text() == "PORT=8000\n"
    assert (install / "runtime" / "marker").read_text() == "kept"
    if sys.platform != "win32":
        assert (install / "setup-mac.command").stat().st_mode & 0o111


def test_update_is_a_no_op_when_current(release):
    install = release["install"]
    (install / "app" / "VERSION").write_text("0.3.0\n")
    assert update.update(install, release["url"]) == update.UP_TO_DATE
    assert (install / "app" / "routes.py").read_text() == "old"


def test_update_refuses_a_swapped_zip(release):
    install = release["install"]
    _make_release_zip(release["dir"] / "biomnikhil.zip", "0.3.0", {"app/routes.py": b"evil"})
    assert update.update(install, release["url"]) == update.NOT_UPDATED
    assert (install / "app" / "routes.py").read_text() == "old"
    assert (install / "app" / "VERSION").read_text().strip() == "0.2.0"


def test_update_refuses_an_unsigned_release(release):
    install = release["install"]
    (release["dir"] / "release.txt.sig").unlink()
    assert update.update(install, release["url"]) == update.NOT_UPDATED
    assert (install / "app" / "routes.py").read_text() == "old"


def test_update_leaves_git_checkouts_alone(release):
    install = release["install"]
    (install / ".git").mkdir()
    assert update.update(install, release["url"]) == update.UP_TO_DATE
    assert (install / "app" / "old_module" / "gone.py").exists()
