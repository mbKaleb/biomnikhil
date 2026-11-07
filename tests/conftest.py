"""Shared setup. Config reads its paths from the environment when app.config
is first imported, so they're pointed at a throwaway folder here, before any
test imports the app: tests never touch a real install's data/."""
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "installer"))

_DATA = Path(tempfile.mkdtemp(prefix="biomnikhil-tests-"))
os.environ["BIOMNI_DATA_PATH"] = str(_DATA)
os.environ["BIOMNI_UPLOAD_PATH"] = str(_DATA / "user" / "uploads")
os.environ["BIOMNI_CHAT_DB"] = str(_DATA / "user" / "chats.db")
os.environ["BIOMNI_OUTPUT_PATH"] = str(_DATA / "user" / "outputs")
os.environ["BIOMNI_INPUT_PATH"] = str(_DATA / "user" / "inputs")
os.environ["BIOMNI_HARNESS_PATH"] = str(_DATA / "user" / "harness")
os.environ["MPLBACKEND"] = "Agg"


def pytest_runtest_logreport(report):
    """On GitHub Actions, each failure also becomes an annotation, which anyone
    can read on the run's page and through the API, unlike the full log."""
    if not os.environ.get("GITHUB_ACTIONS") or not report.failed:
        return
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(report.longrepr))[-3000:]
    # Workflow commands end at a newline, and these characters are escaped.
    message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = report.nodeid.replace("%", "%25").replace(":", "%3A").replace(",", "%2C")
    print(f"\n::error title={title}::{message}", flush=True)


class FakeUser:
    """Stands in for auth.User: signed in, with fixed (fake) AWS credentials."""

    email = "tester@example.org"
    sub = "test-user"
    signed_out = False

    def __init__(self):
        from botocore.credentials import Credentials

        self.credentials = Credentials("AKIDTESTONLY", "test-secret-key")

    def still_valid(self):
        return not self.signed_out


@pytest.fixture
def app():
    from app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def token(app):
    """Headers for a state-changing request from the app's own page."""
    return {"X-Biomnikhil-Token": app.config["CSRF_TOKEN"]}


@pytest.fixture
def signed_in(client):
    from app import auth

    user = FakeUser()
    auth._users["test-session"] = user
    with client.session_transaction() as session:
        session["sid"] = "test-session"
    yield user
    auth._users.pop("test-session", None)
