"""asyncpg is a hash-pinned runtime dependency for both architectures (ADR-031, ADR-034)."""

import pathlib
import re

OP = pathlib.Path(__file__).resolve().parents[2]


def test_asyncpg_is_pinned_with_an_amd64_and_an_arm64_hash():
    text = (OP / "requirements.txt").read_text()
    m = re.search(
        r"^asyncpg==0\.31\.0 \\\n((?:\s+--hash=sha256:[0-9a-f]{64}(?: \\)?\n?)+)",
        text,
        re.MULTILINE,
    )
    assert m, "asyncpg is not pinned"
    hashes = re.findall(r"sha256:([0-9a-f]{64})", m.group(1))
    assert (
        set(hashes)
        == {
            "aad7a33913fb8bcb5454313377cc330fbb19a0cd5faa7272407d8a0c4257b671",  # cp312 manylinux_2_28 x86_64
            "12b3b2e39dc5470abd5e98c8d3373e4b1d1234d9fbdedf538798b2c13c64460a",  # cp312 manylinux_2_28 aarch64
        }
    )


def test_every_runtime_requirement_is_hashed():
    text = (OP / "requirements.txt").read_text()
    names = re.findall(r"^([A-Za-z0-9_.-]+)==", text, re.MULTILINE)
    blocks = re.split(r"^[A-Za-z0-9_.-]+==", text, flags=re.MULTILINE)[1:]
    assert len(names) == len(blocks) and all("--hash=sha256:" in b for b in blocks)


def test_the_dev_file_does_not_pin_asyncpg_a_second_time():
    dev = (OP / "requirements-dev.txt").read_text()
    assert not re.search(r"^asyncpg", dev, re.MULTILINE)
