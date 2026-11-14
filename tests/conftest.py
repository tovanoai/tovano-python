"""Test setup. Integration tests (marked ``server``) run against a real Tovano server started
by ../testing/harness.ts with Bun. They are skipped when TOVANO_SKIP_INTEGRATION=1 or when
Bun or the server checkout is missing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

import pytest

from tovano import AsyncTovano, Tovano

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent.parent / "testing" / "harness.ts"
SERVER_DIR = Path(os.environ.get("TOVANO_SERVER_DIR", HERE.parent.parent.parent / "site")).resolve()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "server: needs the local Tovano test server")


@pytest.fixture(scope="session")
def harness() -> Iterator[Dict[str, str]]:
    if os.environ.get("TOVANO_SKIP_INTEGRATION") == "1":
        pytest.skip("TOVANO_SKIP_INTEGRATION=1")
    if not shutil.which("bun"):
        pytest.skip("Bun is not installed")
    if not (SERVER_DIR / "src" / "app.ts").exists():
        pytest.skip(f"no Tovano server at {SERVER_DIR}")
    proc = subprocess.Popen(
        ["bun", str(HARNESS)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env={**os.environ, "TOVANO_SERVER_DIR": str(SERVER_DIR)},
    )
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if not line:
        proc.kill()
        pytest.fail("The Tovano test server did not start.")
    try:
        yield json.loads(line)
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _control(harness: Dict[str, str], path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    req = urllib.request.Request(
        harness["control_url"] + path,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


@pytest.fixture
def new_account(harness: Dict[str, str]) -> Callable[..., Dict[str, Any]]:
    """Makes a fresh organisation with an API key. Default balance $10."""

    def make(balance_micro: Optional[int] = None, spend_limit_micro: Optional[int] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if balance_micro is not None:
            body["balance_micro"] = balance_micro
        if spend_limit_micro is not None:
            body["spend_limit_micro"] = spend_limit_micro
        acc = _control(harness, "/account", body)
        acc["base_url"] = harness["base_url"]
        return acc

    return make


@pytest.fixture
def control(harness: Dict[str, str]) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    return lambda path, body: _control(harness, path, body)


@pytest.fixture
def account(new_account: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    return new_account()


@pytest.fixture
def client(account: Dict[str, Any]) -> Iterator[Tovano]:
    with Tovano(api_key=account["api_key"], base_url=account["base_url"]) as c:
        yield c


@pytest.fixture
async def aclient(account: Dict[str, Any]) -> Any:
    async with AsyncTovano(api_key=account["api_key"], base_url=account["base_url"]) as c:
        yield c
