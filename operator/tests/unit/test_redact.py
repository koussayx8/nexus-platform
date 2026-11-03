"""The Evidence Redactor (spec §16, Q1): every class of secret is scrubbed, at any depth, and the
counts say what was removed."""

import json

import pytest

from nexus_operator.redact import CLASSES, redact_bundle, redact_text

SECRETS = {
    "token": "ghp_" + "A1b2C3d4E5" * 4,
    "jwt": ".".join(
        [
            "ey" + "JhbGciOiJIUzI1NiJ9",
            "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0",
            "dBjftJeZ4CVPmB92K27uhbUJU1p1r",
        ]
    ),
    "key": "AK" + "IAABCDEFGHIJKLMNOP",  # assembled: no secret-shaped literal in Git
    "email": "alice.admin@example.com",
    "ip4": "10.42.7.19",
    "ip6": "fd00:dead:beef::17",
    "password": "hunter2hunter2",
    "url_credential": "s3cr3tPassw0rd",
    "bearer": "abcDEF1234567890xyz",
    "private_key": "-----BEGIN RSA "
    + "PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA "
    + "PRIVATE KEY-----",
}
LINES = [
    f"using token {SECRETS['token']} for upload",
    f"jwt={SECRETS['jwt']}",
    f"aws key {SECRETS['key']} found",
    f"contact {SECRETS['email']} for access",
    f"connecting to {SECRETS['ip4']}:5432",
    f"peer {SECRETS['ip6']} unreachable",
    f"DB_PASSWORD={SECRETS['password']}",
    f"dsn postgres://app:{SECRETS['url_credential']}@db.internal:5432/x",
    f"Authorization: Bearer {SECRETS['bearer']}",
    f"key follows {SECRETS['private_key']} done",
]


@pytest.mark.parametrize("line", LINES)
def test_each_line_loses_its_secret(line):
    counts: dict[str, int] = {}
    out = redact_text(line, counts)
    for secret in SECRETS.values():
        assert secret not in out
    assert "[REDACTED:" in out
    assert sum(counts.values()) >= 1


def test_the_surrounding_text_survives():
    out = redact_text(
        f"dsn postgres://app:{SECRETS['url_credential']}@db.internal:5432/x", {}
    )
    assert out == "dsn postgres://app:[REDACTED:url_credential]@db.internal:5432/x"


def test_ordinary_evidence_is_left_alone():
    text = (
        "pod sample-api-7d9 Ready 09:12:03 digest sha256:"
        + "ab" * 32
        + " restarts 3 p95 0.31s"
    )
    counts: dict[str, int] = {}
    assert redact_text(text, counts) == text
    assert not counts


def test_a_version_number_is_not_an_address():
    counts: dict[str, int] = {}
    assert (
        redact_text("python 3.12.4 and 300.1.1.1", counts)
        == "python 3.12.4 and 300.1.1.1"
    )


def test_every_string_at_any_depth_is_scrubbed_and_keys_are_kept():
    bundle = {
        "schema": "evidence/v1",
        "logs": {"current": LINES, "previous": [LINES[0]]},
        "pods": [
            {"name": "p", "containers": [{"state": "waiting", "reason": LINES[6]}]}
        ],
        "gitops": {
            "revision": LINES[3],
            "lastCommit": {"sha": "abc", "message": LINES[1]},
        },
        "n": 3,
        "flag": True,
        "none": None,
    }
    out, counts = redact_bundle(bundle)
    blob = json.dumps(out)
    for secret in SECRETS.values():
        assert secret not in blob
        assert secret.replace("\n", "\\n") not in blob
    assert set(out) == set(bundle)
    assert out["n"] == 3 and out["flag"] is True and out["none"] is None
    assert set(counts) == set(CLASSES)
    assert counts["email"] >= 2 and counts["token"] >= 2 and counts["ip"] >= 2
    assert bundle["logs"]["current"][0] == LINES[0]  # the input is not modified


# One line per entry of redact._PATTERNS, plus marker-adjacent shapes that used to be re-matched.
_EVERY_PATTERN = [
    *LINES,
    "ghp_" + "Z9y8X7w6V5" * 3 + " tail",
    "github_pat_" + "A1b2C3d4E5" * 3,
    "xox" + "b-1234567890-abcdefghij",
    "sk-" + "A1b2C3d4E5F6G7H8I9",
    "glpat-" + "A1b2C3d4E5F6G7H8I9",
    "AIza" + "A1b2C3d4E5" * 3 + "xyz",
    "api_key: 'topsecretvalue'",
    "10.0.0.1 and 2001:db8::1",
    "password=hunter2hunter2 token=ghp_" + "Q1w2E3r4T5" * 3,
]


@pytest.mark.parametrize("line", _EVERY_PATTERN)
def test_a_second_pass_changes_nothing_and_counts_nothing(line):
    first: dict[str, int] = {}
    once = redact_text(line, first)
    second: dict[str, int] = {}
    twice = redact_text(once, second)
    assert twice == once
    assert second == {}


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize(
    "shape",
    [
        "{m}",
        "password={m}",
        "secret: {m}",
        'api_key="{m}"',
        "postgres://app:{m}@db:5432/x",
        "Bearer {m}",
        "{m}@example.com",
        "{m} 10.0.0.1",
    ],
)
def test_a_marker_is_never_matched(cls, shape):
    text = shape.format(m=f"[REDACTED:{cls}]")
    counts: dict[str, int] = {}
    out = redact_text(text, counts)
    if "10.0.0.1" not in text:
        assert out == text
        assert counts == {}
    else:
        assert out == f"[REDACTED:{cls}] [REDACTED:ip]"
        assert counts == {"ip": 1}


def test_a_redacted_bundle_redacts_to_itself():
    bundle = {"a": [LINES[6], {"b": LINES[7]}], "c": LINES[0]}
    once, counts = redact_bundle(bundle)
    twice, counts2 = redact_bundle(once)
    assert twice == once
    assert all(v == 0 for v in counts2.values())
    assert sum(counts.values()) >= 3
