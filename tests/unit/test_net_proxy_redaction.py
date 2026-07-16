"""SC-004: the proxy password appears in ZERO bytes of output on every path.

Matrix: every FR-009 outcome (driven by the scripted stand-in proxy) x every output
format (human stdout+stderr, --json, --jsonl), with credentials supplied — plus the
exception str()/repr() and the envelope query echo. The redacted display
(``user:***@``) must appear wherever the proxy is named.
"""

from __future__ import annotations

import json
import socket

import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.net import api
from opskit.net.errors import ProxyError

runner = CliRunner()

USERNAME = "svc"
PASSWORD = "hunter2-Sup3rSecret"
REDACTED = f"{USERNAME}:***@"

PROXY_ENV_VARS = [
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
]

# behavior -> (uses stand-in, expected exit code)
OUTCOMES = [
    ("tunnel", 0),
    ("auth", 14),
    ("deny", 18),
    ("bad-gateway", 19),
    ("gateway-timeout", 19),
    ("garbage", 20),
    ("silent", 6),
]

FORMATS = ["human", "json", "jsonl"]


@pytest.fixture(autouse=True)
def clean_proxy_env(monkeypatch):
    for var in PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _proxy_spec_with_creds(address: str) -> str:
    return f"http://{USERNAME}:{PASSWORD}@{address}"


def _invoke(proxy_arg: str, fmt: str):
    args = [
        "net",
        "check",
        "internal.example:443",
        "--proxy",
        proxy_arg,
        "--timeout",
        "1",
        "--retries",
        "0",
    ]
    if fmt == "json":
        args.append("--json")
    elif fmt == "jsonl":
        args.append("--jsonl")
    return runner.invoke(app, args, env={"NO_COLOR": "1"})


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(("behavior", "expected_exit"), OUTCOMES)
def test_password_never_in_output(scripted_proxy, behavior, expected_exit, fmt):
    proxy = scripted_proxy(behavior, auth=(USERNAME, PASSWORD))
    result = _invoke(_proxy_spec_with_creds(proxy.address), fmt)
    assert result.exit_code == expected_exit
    assert PASSWORD not in result.output
    if fmt in ("json", "jsonl"):
        text = result.output
        if fmt == "jsonl":
            text = text.strip().splitlines()[-1]
        payload = json.loads(text)
        assert payload["route"]["proxy"] == f"{REDACTED}{proxy.address}"
        assert payload["query"]["proxy"] == f"{REDACTED}{proxy.address}"
        assert PASSWORD not in result.output


@pytest.mark.parametrize("fmt", FORMATS)
def test_password_never_in_output_proxy_unreachable(fmt):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    dead_port = int(probe.getsockname()[1])
    probe.close()
    result = _invoke(_proxy_spec_with_creds(f"127.0.0.1:{dead_port}"), fmt)
    assert result.exit_code in (8, 6)  # refused vs timeout: platform-dependent
    assert PASSWORD not in result.output


@pytest.mark.parametrize("fmt", FORMATS)
def test_password_never_in_output_proxy_unresolvable(fmt):
    result = _invoke(_proxy_spec_with_creds("no-such-proxy.invalid:3128"), fmt)
    assert result.exit_code == 3
    assert PASSWORD not in result.output


def test_password_never_in_exception_text(scripted_proxy):
    proxy = scripted_proxy("deny", auth=(USERNAME, PASSWORD))
    with pytest.raises(ProxyError) as excinfo:
        api.check(
            "internal.example:443",
            proxy=_proxy_spec_with_creds(proxy.address),
            timeout=1.0,
            retries=0,
        )
    exc = excinfo.value
    rendered = str(exc) + repr(exc) + exc.message + (exc.hint or "")
    assert PASSWORD not in rendered
    assert REDACTED in exc.message


def test_password_never_in_usage_error_echo():
    # A malformed spec still carrying credentials must not echo the password.
    result = runner.invoke(
        app,
        [
            "net",
            "check",
            "internal.example:443",
            "--proxy",
            f"socks5://{USERNAME}:{PASSWORD}@p.corp:1080",
        ],
    )
    assert result.exit_code == 2
    assert PASSWORD not in result.output


def test_env_sourced_credentials_redacted(scripted_proxy, monkeypatch):
    proxy = scripted_proxy("deny", auth=(USERNAME, PASSWORD))
    monkeypatch.setenv("HTTPS_PROXY", _proxy_spec_with_creds(proxy.address))
    result = runner.invoke(
        app,
        ["net", "check", "internal.example:443", "--timeout", "1", "--json"],
    )
    assert result.exit_code == 18
    assert PASSWORD not in result.output
    payload = json.loads(result.output)
    assert payload["route"]["source"] == "env:HTTPS_PROXY"
    assert payload["route"]["proxy"] == f"{REDACTED}{proxy.address}"
