"""Shared pytest fixtures for browser UI tests.

Prerequisites (browser tests only):
  pip install pytest-playwright
  playwright install chromium
"""

import gzip
import shutil
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_WEB  = _REPO / "web"


@pytest.fixture(scope="session")
def browser_server(tmp_path_factory):
    """Serve web/ from a temp directory on an ephemeral port.

    Copies index.html, config.json, and thesaurus.db.gz into a clean temp
    directory then starts a local HTTP server for the duration of the session.

    Yields:
        Base URL string, e.g. ``"http://localhost:9877"``.
    """
    db_gz = _WEB / "thesaurus.db.gz"
    if not db_gz.exists():
        pytest.skip("web/thesaurus.db.gz not found — run build.sh (or make_browser_db.py + gzip) first")

    tmp = tmp_path_factory.mktemp("browser_serve")
    for name in ("index.html", "config.json"):
        src = _WEB / name
        if src.exists():
            shutil.copy(src, tmp / name)
    shutil.copy(db_gz, tmp / "thesaurus.db.gz")

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(tmp), **kwargs)

        def log_message(self, *args):  # silence request logs during tests
            pass

    server = HTTPServer(("localhost", 9877), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://localhost:9877"
    server.shutdown()
