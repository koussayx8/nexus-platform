"""Commit validation and the mechanical revert (ADR-041 decisions 6, 7, 11; checks 1-6).

The first failing check ends the attempt, names the check, and nothing is pushed. The diff is
`git revert --no-commit <sha>` computed here; no Reasoner output reaches the diff, the commit
message, the branch name or the title. Under rule A1 the newest overlay commit is the only
candidate tried: if it fails a check, no older commit is.
"""

import fnmatch
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml

from .config import Config
from .gitcmd import Git, GitError
from .github import GitHub, GitHubError
from .trigger import Request

SHA = re.compile(r"[0-9a-f]{40}")
BRANCH_PREFIX = "nexus/revert-"
KUSTOMIZATION = "kustomization.yaml"
MUTABLE_KEYS = frozenset({"patches"})  # check 4c: the only kustomization key a revert may change


def _kustomization(git: Git, rev: str, path: str) -> dict[str, Any] | None:
    """The overlay's kustomization at `rev` ("" = the index), parsed; None if absent or not a mapping."""
    res = git(["show", f"{rev}:{path}"], check=False)
    if res.rc != 0:
        return None
    try:
        doc = yaml.safe_load(res.out)
    except yaml.YAMLError:
        return None
    return doc if isinstance(doc, dict) else None


def kustomization_change(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    """The keys other than `patches` that differ (resources, namespace, labels, images, ...)."""
    if before is None or after is None:
        return ["<the whole file>"]
    keys = (set(before) | set(after)) - MUTABLE_KEYS
    return sorted(k for k in keys if before.get(k) != after.get(k))


@dataclass(frozen=True)
class Refusal:
    check: str
    text: str


@dataclass(frozen=True)
class Prepared:
    sha: str
    subject: str
    chosen_by_reasoner: bool
    branch: str  # in the fork
    base: str  # upstream
    revert_commit: str


def branch_for(req: Request) -> str:
    return f"{BRANCH_PREFIX}{req.name}"


def _git_ok(git: Git, *args: str) -> bool:
    return git(list(args), check=False).rc == 0


def allowed(path: str, patterns: tuple[str, ...]) -> bool:
    """fnmatch, but `*` never crosses a `/`: the pattern and the path have the same depth."""
    return any(fnmatch.fnmatchcase(path, p) and path.count("/") == p.count("/") for p in patterns)


def prepare(req: Request, cfg: Config, git: Git, gh: GitHub, now: datetime) -> Prepared | Refusal:
    """Fetch, choose, check and commit the revert in the scratch repository. Pushes nothing."""
    t = req.target
    remote_ref = f"refs/remotes/upstream/{t.branch}"
    try:
        git(["init", "-q"])
        git(["remote", "add", "upstream", cfg.upstream_url])
        git(["fetch", "-q", "--no-tags", "--no-recurse-submodules", f"--depth={cfg.fetch_depth}",
             "upstream", f"+refs/heads/{t.branch}:{remote_ref}"])
        tip = git(["rev-parse", "--verify", "--end-of-options", f"{remote_ref}^{{commit}}"]).out.strip()
        if req.named_sha is None:  # rule A1
            cand = git(["log", "-1", "--format=%H", tip, "--", t.overlay]).out.strip()
            if not cand:
                return Refusal("A1", f"no commit touching {t.overlay} in the last "
                                     f"{cfg.fetch_depth} on {t.branch}")
        else:
            cand = req.named_sha
    except GitError as e:
        return Refusal("fetch", f"cannot fetch {t.branch} from upstream: {e}")

    # 1. a full 40-hex SHA: no ref, range or option-like string reaches git
    if not SHA.fullmatch(cand):
        return Refusal("1", "the candidate is not a full 40-hex SHA")
    # 2. on the tracked branch, within the fetched depth
    if not _git_ok(git, "cat-file", "-e", f"{cand}^{{commit}}") or not _git_ok(
            git, "merge-base", "--is-ancestor", cand, tip):
        return Refusal("2", f"{cand} is not among the last {cfg.fetch_depth} commits of {t.branch}")
    try:
        # 2, "recent": also committed within maxAgeHours (owner, 2026-11-19), by the committer date
        committed = datetime.fromtimestamp(int(git(["log", "-1", "--format=%ct", cand]).out.strip()), UTC)
        if now - committed > timedelta(hours=cfg.max_age_hours):
            return Refusal("2", f"{cand} was committed more than {cfg.max_age_hours:g} h ago")
        parents = git(["rev-list", "--parents", "-n", "1", cand]).out.split()[1:]
        # 3. exactly one parent (a merge needs a chosen parent; the shallow boundary shows none)
        if len(parents) != 1:
            return Refusal("3", f"{cand} has {len(parents)} parents here, not exactly one")
        files = [f for f in git(["diff-tree", "--no-commit-id", "--no-renames", "--name-only", "-r", "-z",
                                 cand]).out.split("\0") if f]
        # 4. changes at least one file, every one under the overlay (A2)
        if not files:
            return Refusal("4", f"{cand} changes no file")
        outside = [f for f in files if not f.startswith(t.overlay + "/")]
        if outside:
            return Refusal("4", f"{cand} changes {len(outside)} file(s) outside {t.overlay}")
        # 4b. only the workload files `allow` names, never an excluded one (the Namespace's level)
        hit = sorted(f for f in files if f in t.exclude or not allowed(f, t.allow))
        if hit:
            return Refusal("4b", f"{cand} changes a file a revert may not touch ({', '.join(hit)})")
        subject = git(["log", "-1", "--format=%s", cand]).out.strip()
        # 5. the revert applies cleanly and is not empty
        git(["checkout", "-q", "--detach", tip])
        if git(["revert", "--no-commit", cand], check=False).rc != 0:
            git(["revert", "--abort"], check=False)
            return Refusal("5", f"reverting {cand} conflicts with {t.branch}")
        if _git_ok(git, "diff", "--cached", "--quiet"):
            return Refusal("5", f"reverting {cand} changes nothing (already reverted?)")
        # 4c. the overlay's kustomization keeps every key but `patches`, compared as YAML before
        # and after the revert: resources (which lists the Namespace), namespace, labels, images...
        kpath = f"{t.overlay}/{KUSTOMIZATION}"
        if kpath in files:
            changed = kustomization_change(_kustomization(git, tip, kpath), _kustomization(git, "", kpath))
            if changed:
                git(["revert", "--abort"], check=False)
                return Refusal("4c", f"reverting {cand} would change {kpath} beyond patches: "
                                     f"{', '.join(changed)}")
        git(["-c", f"user.name={cfg.author_name}", "-c", f"user.email={cfg.author_email}",
             "commit", "-q", "--no-verify", "-m", f"revert({t.overlay}): revert {cand}",
             "-m", f"This reverts commit {cand}."])
        revert_commit = git(["rev-parse", "HEAD"]).out.strip()
    except (GitError, ValueError) as e:
        return Refusal("git", str(e))

    # 6. one open revert pull request per branch, and a fresh working branch in the fork
    branch = branch_for(req)
    try:
        mine = [p for p in gh.open_pulls(t.branch)
                if ((p.get("head") or {}).get("user") or {}).get("login", "").lower() == cfg.machine.lower()
                and str((p.get("head") or {}).get("ref", "")).startswith(BRANCH_PREFIX)]
        if mine:
            n = mine[0].get("number")
            return Refusal("6", f"revert pull request #{n} is already open against {t.branch}")
        if gh.branch_exists(cfg.fork, branch):
            return Refusal("6", f"{branch} already exists in the fork")
    except GitHubError as e:
        return Refusal("6", f"cannot check open pull requests: {e}")
    return Prepared(sha=cand, subject=subject, chosen_by_reasoner=req.named_sha is not None,
                    branch=branch, base=t.branch, revert_commit=revert_commit)


def push(p: Prepared, cfg: Config, git: Git) -> None:
    """Push the one revert commit to the fork, under nexus/ only, never forced."""
    assert p.branch.startswith(BRANCH_PREFIX) and SHA.fullmatch(p.revert_commit)
    git(["remote", "add", "fork", cfg.fork_url])
    git(["push", "-q", "--no-verify", "fork", f"{p.revert_commit}:refs/heads/{p.branch}"], push_auth=True)
