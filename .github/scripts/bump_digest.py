"""Rewrite one image digest in a manifest, for the digest-bump pull request (spec §19).

    bump_digest.py <file> <image> <sha256:digest>

Two shapes are understood, and exactly one match is required (anything else is an error, so a
manifest that changed shape does not get a silent no-op):

  * a kustomize `images:` entry:      - name: <image>   followed by   digest: sha256:...
  * a pod spec reference:             image: <image>@sha256:...

Standard library only.
"""

import re
import sys

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def bump(text: str, image: str, digest: str) -> str:
    if not DIGEST.fullmatch(digest):
        raise ValueError(f"not a sha256 digest: {digest!r}")
    esc = re.escape(image)
    patterns = [
        # kustomize: `name: <image>`, then `digest:` as the next key of the same entry.
        re.compile(
            rf"(?m)(^[ \t]*-?[ \t]*name:[ \t]*{esc}[ \t]*\n(?:[ \t]*[a-zA-Z]+:[^\n]*\n)*?[ \t]*digest:[ \t]*)sha256:[0-9a-f]{{64}}"
        ),
        # pod spec: `image: <image>@sha256:...`
        re.compile(rf"(?m)(^[ \t]*image:[ \t]*{esc}@)sha256:[0-9a-f]{{64}}"),
    ]
    hits = [(p, len(p.findall(text))) for p in patterns]
    total = sum(n for _, n in hits)
    if total != 1:
        raise ValueError(
            f"expected exactly one digest reference for {image}, found {total}"
        )
    for p, n in hits:
        if n:
            return p.sub(lambda m: m.group(1) + digest, text)
    raise AssertionError("unreachable")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    path, image, digest = argv[1:]
    with open(path, encoding="utf-8") as f:
        old = f.read()
    try:
        new = bump(old, image, digest)
    except ValueError as e:
        print(f"bump_digest: {path}: {e}", file=sys.stderr)
        return 1
    if new == old:
        print(f"bump_digest: {path}: already at {digest}", file=sys.stderr)
        return 3
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"{path}: {image} -> {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
