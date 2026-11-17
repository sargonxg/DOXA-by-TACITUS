"""Isolated browser-test server; never starts a model or remote inference run."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def chromium_launch_options() -> dict:
    """Use an explicit/system Chromium or Playwright's managed browser in CI."""
    options = {"headless": True, "args": ["--no-sandbox"]}
    configured = os.environ.get("DOXA_BROWSER_EXECUTABLE")
    if configured:
        options["executable_path"] = configured
    elif Path("/usr/bin/chromium").is_file():
        options["executable_path"] = "/usr/bin/chromium"
    return options


@contextmanager
def local_app(directory: Path | None = None, *, allow_external: bool = False):
    external = os.environ.get("DOXA_BROWSER_URL") if allow_external else None
    if external:
        yield external.rstrip("/")
        return
    temporary = tempfile.TemporaryDirectory(prefix="doxa-browser-") if directory is None else None
    data = directory or Path(temporary.name)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    server_code = 'import os, uvicorn; from doxa.api import create_app; uvicorn.run(create_app(start_workers=False), host="127.0.0.1", port=int(os.environ["DOXA_BROWSER_PORT"]))'
    process = subprocess.Popen(
        [sys.executable, "-c", server_code],
        cwd=ROOT,
        env={**os.environ, "DOXA_DATA_DIR": str(data), "DOXA_BROWSER_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("Isolated browser test API exited before readiness")
            try:
                with urlopen(f"{base}/api/health", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Isolated browser test API did not become ready")
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)
        if temporary:
            temporary.cleanup()
