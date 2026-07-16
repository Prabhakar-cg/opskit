# Quickstart: validating proxy-aware reachability checks

Runnable scenarios proving the feature end-to-end. Contracts: [cli.md](contracts/cli.md),
[python-api.md](contracts/python-api.md); model: [data-model.md](data-model.md).

## Prerequisites

```bash
uv sync --extra dev
```

No real proxy is required: the deterministic path uses the in-process stand-in proxy from
the test suite (research R9). Scenarios against a real corporate proxy are optional and
marked as such.

## 1. Full deterministic validation (the gate)

```bash
uv run pytest tests/unit/test_net_proxy_spec.py \
              tests/unit/test_net_proxy.py \
              tests/unit/test_net_api_proxy.py \
              tests/unit/test_net_cli_proxy.py \
              tests/unit/test_net_proxy_redaction.py \
              tests/integration/test_net_proxy_loopback.py -q
uv run pytest -q                      # whole suite still ≥ 90% coverage, direct behavior unchanged
```

**Expected**: all pass; the redaction module asserts the password string appears in zero
outputs across every verdict × {human, --json, --jsonl} (SC-004); the loopback integration
module exercises every FR-009 outcome against the scripted stand-in proxy (SC-002).

## 2. Usage-error surface (no network, instant)

```bash
uv run opskit net check example.com:443 --proxy socks5://p:1080 ; echo "exit=$?"   # → usage error naming the scheme, exit 2
uv run opskit net check example.com:443 --proxy proxy.corp                          # → missing proxy port, exit 2
uv run opskit net check ntp.example.com:123 --udp --proxy proxy.corp:3128           # → "HTTP proxies cannot tunnel UDP", exit 2
uv run opskit net check example.com:443 --proxy proxy.corp:3128 --direct            # → conflicting flags, exit 2
```

## 3. Failure attribution against a dead proxy (loopback, deterministic)

```bash
# Nothing listens on 127.0.0.1:39999 → the PROXY hop fails, attribution must say so:
uv run opskit net check example.com:443 --proxy 127.0.0.1:39999 --timeout 2 --retries 0 ; echo "exit=$?"
```

**Expected**: verdict wording names the **proxy** (`cannot connect to proxy 127.0.0.1:39999 …`),
hint points at the proxy setting, exit `8` (refused; on Windows this may classify as timeout
`6` — either way the message attributes the proxy hop, which is what tests assert).

## 4. Env fallback, precedence, and route disclosure

```bash
HTTPS_PROXY=http://127.0.0.1:39999 uv run opskit net check example.com:443 --timeout 2 --retries 0 --json \
  | python -c "import json,sys; r=json.load(sys.stdin); print(r['route'])"
```

**Expected**: `route.via` reflects the proxied attempt with `source: "env:HTTPS_PROXY"`.
Then confirm each override:

```bash
HTTPS_PROXY=http://127.0.0.1:39999 uv run opskit net check example.com:443 --direct --json   # route.via=direct
HTTPS_PROXY=http://127.0.0.1:39999 NO_PROXY=example.com uv run opskit net check example.com:443 --json \
  # route.source=no-proxy-exemption, checked directly
```

## 5. Credential redaction spot-check (belt to the test suite's braces)

```bash
uv run opskit net check example.com:443 --proxy http://svc:hunter2@127.0.0.1:39999 --timeout 2 --retries 0 --json 2>&1 \
  | grep -c hunter2      # MUST print 0
```

## 6. Backward compatibility

```bash
uv run opskit net check example.com:443 --json | python -c \
  "import json,sys; r=json.load(sys.stdin); assert r['route']=={'via':'direct','proxy':None,'source':'default'}; print('route ok')"
uv run pytest tests/ -q -k "net and not proxy"   # pre-existing net tests pass unchanged
```

**Expected**: direct human output byte-identical to the previous release; machine output
differs only by the always-present `route` field (SC-006).

## 7. Optional: real corporate proxy smoke (never gates CI)

```bash
uv run pytest -m network tests/ -q               # opt-in @pytest.mark.network tests
# or manually, on a proxy-only network:
uv run opskit net check github.com:443 --proxy "$HTTPS_PROXY"
```

**Expected**: `open (via …)` with tunnel time where the proxy allows 443; `tunnel denied`
(exit 18) for a policy-blocked port; `target unreachable via proxy` (exit 19) for a dead
internal host — each verdict telling you which hop to investigate (SC-003).
