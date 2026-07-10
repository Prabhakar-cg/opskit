# Quickstart Validation: Active Directory / LDAP Diagnostics

Runnable scenarios proving the feature end-to-end. CI-parity checks (1–6) run against the
offline mock/loopback layers via pytest; live checks (7) need a reachable domain and are
opt-in.

## Prerequisites

```bash
uv sync --extra dev          # dev extra includes ldap3
uv run pytest tests/unit/test_ad_*.py tests/integration/test_ad_mock_directory.py tests/integration/test_ad_loopback.py
```

## 1. Missing-extra behavior (base install stays slim)

```bash
uv run python -c "import opskit.ad"                    # succeeds without ldap3
# In an env without the extra: any command fails actionably —
opskit ad user jdoe -s dc01   # → exit 2, hint: pip install "opskit[ad]"
```

## 2. Account status (US1)

```bash
export OPSKIT_AD_SERVER=dc01.corp.example.com OPSKIT_AD_USER=ops@corp.example.com
export OPSKIT_AD_PASSWORD=...   # or omit → hidden prompt
opskit ad user jdoe
```

Expected: verdict lines for enabled/locked/password/account expiry; **all** blockers listed
(mock fixtures cover disabled+locked simultaneously); exit 0; `--json` envelope matches
[contracts/cli.md](contracts/cli.md); unknown principal → exit 16 with hint.

## 3. Membership (US2)

```bash
opskit ad groups jdoe                 # direct + primary group
opskit ad groups jdoe --effective     # nested, cycle-safe, paths shown
opskit ad member jdoe "VPN Users"     # verdict + chain; exit 0
opskit ad member jdoe "Domain Admins" # exit 17 (not a member)
```

Expected against the mock topology: nested group acquired via the fixture's chain appears
marked `nested` with the shortest path; the cycle fixture terminates; the >1000-member
group lists completely.

## 4. Connectivity check (US3)

```bash
opskit ad check dc01.corp.example.com            # staged report, exit 0
opskit ad check -d corp.example.com              # SRV discovery; reports server used
opskit ad check closed.example.com               # exit 8/6 (refused/timeout), no cred mention
opskit ad check self-signed.local                # exit 10, hint → opskit tls
OPSKIT_AD_PASSWORD=wrong opskit ad check dc01    # exit 14, decoded AD reason in hint
```

## 5. Batch + machine output (US4, Art. IX)

```bash
printf 'jdoe\nasmith\nno-such-user\n' | opskit ad user -i - --jsonl
opskit ad show jdoe "VPN Users" wks-042$          # mixed users/groups/computer, one session
printf 'jdoe\nVPN Users\n' | opskit ad show -i - --jsonl
```

Expected: three NDJSON envelopes (failure included with `result: null`), exit 7 (PARTIAL);
all-healthy input → exit 0. `ad show` batch renders one attribute table per object in human
mode (user rows include email; group rows include the member table) and one envelope per
name under `--jsonl`. Password prompt never triggers on piped stdin (usage error naming
`OPSKIT_AD_PASSWORD` if no credential source).

## 6. Redaction & security gates (Art. III / SC-006)

```bash
uv run pytest tests/ -k ad          # includes the suite-wide password-scan fixture
opskit ad user jdoe --json | grep -c "$OPSKIT_AD_PASSWORD"   # → 0
uv run ruff check src/opskit/ad && uv run mypy src && uv run pyright
```

Expected: no output stream ever contains the secret; strict typing passes with ldap3
quarantined to `ad/directory.py`.

## 7. Live smoke (optional, never gates CI)

```bash
uv run pytest -m network tests/integration/test_ad_network.py
```

Runs `check`/`user`/`groups` against a real DC using `OPSKIT_AD_*` env; validates the mock
layer's assumptions (constructed attributes, AD bind sub-codes) against reality.

## Docs gate

`uv run pytest tests/unit/test_docs_coverage.py` — fails until all five commands have help
text + entries in `src/opskit/ad/README.md` linked from the root README Commands table.
