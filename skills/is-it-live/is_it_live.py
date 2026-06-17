#!/usr/bin/env python3
"""
is-it-live — a read-only map of where every recent change stands, from
"I just typed it" (dirty working tree) to "real users see it" (live in production).

Design goal: a smart non-engineer should read the output in 5 seconds and KNOW
what is live, what is stuck, and the single next action to unstick it.

HARD RULE: this tool is read-only with respect to your repo and your deploys.
It never runs git add/commit/push/merge/checkout/reset, never touches deploys,
never edits tracked files. The only writes it ever does:
  - an HTML report into a cache directory (--html)
  - a starter config file in the repo root, only when you explicitly run --init
  - (--fetch updates your local remote-tracking refs, the same as `git fetch`)

The Ladder (every change climbs these rungs):
  8 LIVE          proven inside the production build real users get
  7 DEPLOYED      in a deployed build, but not (fully) production
  6 MERGED        folded into the deploy branch (usually `main`)
  5 IN A PR       proposed to merge, waiting on review/tests
  4 PUSHED        on GitHub, but on a side branch
  3 COMMITTED     saved, but only on this machine
  2 STAGED        marked to be saved
  1 WORKING TREE  raw edits, saved nowhere

Honesty contract: rungs 1-6 are certain (pure local git). Rungs 7-8 are only
claimed when EVIDENCE proves them; otherwise the tool says "not checked" or
"unverified" — it never converts "I didn't look" into "it is not live".

Evidence sources for rungs 7-8 (all read-only, all optional):
  - a /version endpoint you configure (strongest: the running app tells you its SHA)
  - GitHub Deployments API records (Vercel and many providers write these; zero config)
  - the newest successful run of a deploy workflow in GitHub Actions
  - production URL reachability + served bundle fingerprint (weak: proves "something
    is up", not which commit)

Stdlib only. No third-party dependencies.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone

__version__ = "2.1.1"

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
CENTRAL_CONFIG = os.path.join(SKILL_DIR, "config.json")

HEX_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
STALE_BRANCH_DAYS = 14
REPORT_MAX_AGE_DAYS = 30
MAX_PROBE_URLS = 12  # cap network probes from (possibly untrusted) config

# Rung definitions: number -> (short label, plain-English meaning)
RUNGS = {
    8: ("LIVE", "proven inside the production build"),
    7: ("DEPLOYED", "in a deployed build, not (fully) production"),
    6: ("MERGED", "folded into the deploy branch"),
    5: ("IN A PR", "proposed to merge, waiting on review/tests"),
    4: ("PUSHED", "on GitHub, but on a side branch"),
    3: ("COMMITTED", "saved, but only on this machine"),
    2: ("STAGED", "marked to be saved"),
    1: ("WORKING TREE", "raw edits, saved nowhere"),
}


# --------------------------------------------------------------------------- #
# Shell helpers (all read-only)
# --------------------------------------------------------------------------- #
def run(args, cwd=None, timeout=30, env_extra=None):
    """Run a command; return (returncode, stdout, stderr). Never raises.

    stdout keeps leading whitespace (git porcelain output is column-sensitive);
    only the trailing newline is removed.
    """
    env = None
    if env_extra:
        env = dict(os.environ)
        env.update(env_extra)
    try:
        p = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
        )
        return p.returncode, p.stdout.rstrip("\n"), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"{args[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def git(args, cwd, timeout=30, env_extra=None):
    return run(["git", "-C", cwd] + args, timeout=timeout, env_extra=env_extra)


def git_ok(args, cwd):
    """Return stripped stdout if the git command succeeded, else None."""
    code, out, _ = git(args, cwd)
    return out.strip() if code == 0 else None


def gh_json(args, cwd, timeout=20):
    """Run a gh command expected to print JSON; return parsed value or None."""
    if shutil.which("gh") is None:
        return None
    code, out, _ = run(["gh"] + args, cwd=cwd, timeout=timeout)
    if code != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:  # noqa: BLE001
        return None


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(repo_root):
    """Repo-local .is-it-live.json (preferred) merged over central config.json.

    Central config is keyed by absolute repo path (~ is expanded).
    """
    cfg = {}
    if os.path.exists(CENTRAL_CONFIG):
        try:
            with open(CENTRAL_CONFIG) as f:
                central = json.load(f)
            for key, val in central.items():
                if key.startswith("_") or not isinstance(val, dict):
                    continue
                try:
                    expanded = os.path.expanduser(key)
                    if os.path.realpath(expanded) == os.path.realpath(repo_root):
                        cfg.update(val)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
    local = os.path.join(repo_root, ".is-it-live.json")
    if os.path.exists(local):
        try:
            with open(local) as f:
                cfg.update(json.load(f))
        except Exception:  # noqa: BLE001
            pass
    return cfg


def version_sources(cfg):
    """Normalize config to a list of {label, url, json_path} version sources.

    Supports the new `version_sources` list plus the legacy single
    `version_url` / `version_json_path` pair.
    """
    sources = []
    for src in cfg.get("version_sources") or []:
        if isinstance(src, dict) and src.get("url"):
            sources.append({
                "label": src.get("label") or "version",
                "url": src["url"],
                "json_path": src.get("json_path"),
            })
    if cfg.get("version_url") and not any(s["url"] == cfg["version_url"] for s in sources):
        sources.append({
            "label": "version",
            "url": cfg["version_url"],
            "json_path": cfg.get("version_json_path"),
        })
    return sources


# --------------------------------------------------------------------------- #
# Git state gathering
# --------------------------------------------------------------------------- #
def repo_root(start):
    code, out, _ = run(["git", "-C", start, "rev-parse", "--show-toplevel"])
    return out.strip() if code == 0 else None


def current_branch(root):
    out = git_ok(["rev-parse", "--abbrev-ref", "HEAD"], root)
    return out or "HEAD"


def has_origin(root):
    out = git_ok(["remote"], root)
    return bool(out) and "origin" in out.split()


def origin_slug(root):
    """'owner/repo' parsed from the origin URL, or None."""
    url = git_ok(["remote", "get-url", "origin"], root)
    if not url:
        return None
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def detect_default_branch(root, configured):
    if configured:
        return configured
    out = git_ok(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], root)
    if out:
        return out.split("/")[-1]
    for cand in ("main", "master"):
        if git_ok(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{cand}"], root) is not None:
            return cand
    for cand in ("main", "master"):
        if git_ok(["rev-parse", "--verify", "--quiet", f"refs/heads/{cand}"], root) is not None:
            return cand
    return "main"


def merged_ref(root, default):
    """The ref we treat as 'production source'. Prefer the remote copy."""
    if git_ok(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{default}"], root) is not None:
        return f"origin/{default}", True
    if git_ok(["rev-parse", "--verify", "--quiet", f"refs/heads/{default}"], root) is not None:
        return default, False
    return None, False


def working_tree_status(root):
    """Parse `git status --porcelain` without corrupting column-sensitive lines.

    Returns (staged, unstaged, untracked, detail) where detail breaks the
    unstaged set into modified/deleted for honest reporting.
    """
    code, out, _ = git(["status", "--porcelain=v1"], root)
    staged, unstaged, untracked = [], [], []
    detail = {"modified": 0, "deleted": 0}
    if code == 0 and out:
        for line in out.split("\n"):
            if len(line) < 4:
                continue
            x, y, path = line[0], line[1], line[3:]
            if x in ("R", "C") and " -> " in path:
                path = path.split(" -> ")[-1]  # the file lives at the NEW path now
            if x == "?" and y == "?":
                untracked.append(path)
                continue
            if x not in (" ", "?"):
                staged.append(path)
            if y not in (" ", "?"):
                unstaged.append(path)
                if y == "D":
                    detail["deleted"] += 1
                else:
                    detail["modified"] += 1
    return staged, unstaged, untracked, detail


def ahead_behind(root):
    """(ahead, behind) of current branch vs its upstream, or None if no upstream."""
    code, out, _ = git(["rev-list", "--left-right", "--count", "@{upstream}...HEAD"], root)
    if code != 0 or not out.strip():
        return None
    parts = out.split()
    if len(parts) != 2:
        return None
    behind, ahead = int(parts[0]), int(parts[1])
    return ahead, behind


class AncestryCache:
    """Memoized `git merge-base --is-ancestor` lookups."""

    def __init__(self, root):
        self.root = root
        self._cache = {}

    def is_ancestor(self, sha, ref):
        if not sha or not ref:
            return False
        key = (sha, ref)
        if key not in self._cache:
            code, _, _ = git(["merge-base", "--is-ancestor", sha, ref], self.root)
            self._cache[key] = code == 0
        return self._cache[key]


def resolve_commit(root, sha_like):
    """Full local SHA for a (possibly short) commit id, or None if unknown here."""
    if not sha_like:
        return None
    return git_ok(["rev-parse", "--verify", "--quiet", f"{sha_like}^{{commit}}"], root)


def remote_refs_containing(root, sha):
    code, out, _ = git(["branch", "-r", "--contains", sha, "--format=%(refname:short)"], root)
    if code != 0 or not out:
        return []
    return [r.strip() for r in out.split("\n") if r.strip()]


def recent_commits(root, limit):
    code, out, _ = git(
        ["log", f"-n{limit}", "--no-merges", "--pretty=%h%x1f%H%x1f%s"], root
    )
    commits = []
    if code == 0 and out:
        for line in out.split("\n"):
            parts = line.split("\x1f")
            if len(parts) == 3:
                commits.append({"sha": parts[0], "full_sha": parts[1], "subject": parts[2]})
    return commits


def local_branches_with_meta(root):
    """[{name, tip, age_days}] for every local branch."""
    code, out, _ = git(
        ["for-each-ref", "refs/heads",
         "--format=%(refname:short)%09%(objectname)%09%(committerdate:unix)"],
        root,
    )
    branches = []
    if code != 0 or not out:
        return branches
    now = datetime.now(timezone.utc).timestamp()
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        try:
            age_days = max(0.0, (now - int(parts[2])) / 86400.0)
        except ValueError:
            age_days = None
        branches.append({"name": parts[0], "tip": parts[1], "age_days": age_days})
    return branches


def unmerged_branch_names(root, mref):
    if not mref:
        return None
    code, out, _ = git(["branch", "--no-merged", mref, "--format=%(refname:short)"], root)
    if code != 0:
        return None
    return [b.strip() for b in out.split("\n") if b.strip()]


def worktrees(root):
    code, out, _ = git(["worktree", "list", "--porcelain"], root)
    trees = []
    if code != 0 or not out:
        return trees
    cur = {}
    for line in out.split("\n"):
        if line.startswith("worktree "):
            if cur:
                trees.append(cur)
            cur = {"path": line[len("worktree "):], "detached": False, "branch": None}
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            if ref.startswith("refs/heads/"):
                ref = ref[len("refs/heads/"):]
            cur["branch"] = ref
        elif line.strip() == "detached":
            cur["detached"] = True
    if cur:
        trees.append(cur)
    for t in trees:
        p = t.get("path", "")
        t["agent_scratch"] = "/worktrees/" in p or "/.conductor/" in p
    return trees


def commits_ahead_of_main(root, mref):
    if not mref:
        return None
    code, out, _ = git(["rev-list", "--count", f"{mref}..HEAD"], root)
    if code != 0:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def head_sha_of(root, ref):
    return git_ok(["rev-parse", ref], root)


def human_age(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def remote_sync_state(root, default, use_net):
    """How fresh is our picture of GitHub?

    Preferred: a live `git ls-remote` (sub-second, truly read-only, no ref writes)
    comparing the remote tip of the default branch to our local origin/<default>.
    Fallback (offline): mtime of the remote-tracking ref in the COMMON git dir
    (correct inside linked worktrees).

    Returns {state: in_sync|behind|offline|no_remote|unknown, label, remote_sha}.
    """
    if not has_origin(root):
        return {"state": "no_remote", "label": "local-only repo (no remote)", "remote_sha": None}

    local = git_ok(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{default}"], root)

    if use_net:
        code, out, _ = git(
            ["ls-remote", "origin", "--", f"refs/heads/{default}"],
            root, timeout=10, env_extra={"GIT_TERMINAL_PROMPT": "0"},
        )
        if code == 0 and out.strip():
            remote_sha = out.split()[0]
            if local and remote_sha == local:
                return {"state": "in_sync",
                        "label": "GitHub picture is current ✓ (verified just now)",
                        "remote_sha": remote_sha}
            return {"state": "behind",
                    "label": f"origin/{default} has moved to {remote_sha[:9]} — local copy is behind, run with --fetch",
                    "remote_sha": remote_sha}

    # Offline fallback: mtime in the common dir (works in linked worktrees too)
    common = git_ok(["rev-parse", "--git-common-dir"], root)
    if common and not os.path.isabs(common):
        common = os.path.join(root, common)
    label = "freshness unknown"
    if common:
        candidates = [
            os.path.join(common, "refs", "remotes", "origin", default),
            os.path.join(common, "FETCH_HEAD"),
        ]
        gitdir = git_ok(["rev-parse", "--absolute-git-dir"], root)
        if gitdir:
            candidates.append(os.path.join(gitdir, "FETCH_HEAD"))
        mtimes = [os.path.getmtime(p) for p in candidates if os.path.exists(p)]
        if mtimes:
            age = datetime.now().timestamp() - max(mtimes)
            label = f"last synced with GitHub {human_age(age)} (offline — not re-checked)"
        else:
            label = "never synced with GitHub (offline)"
    return {"state": "offline", "label": label, "remote_sha": None}


# --------------------------------------------------------------------------- #
# GitHub evidence (via gh, optional, all read-only)
# --------------------------------------------------------------------------- #
def fetch_open_prs(root, use_net):
    """All open PRs with head SHAs + CI rollup, in ONE gh call."""
    if not use_net:
        return []
    prs = gh_json(
        ["pr", "list", "--state", "open", "--limit", "50",
         "--json", "number,title,state,headRefName,headRefOid,reviewDecision,statusCheckRollup,url"],
        cwd=root,
    )
    return prs or []


def fetch_merged_prs(root, use_net):
    """Recently merged PRs (head SHA + squash/merge commit), in ONE gh call."""
    if not use_net:
        return []
    prs = gh_json(
        ["pr", "list", "--state", "merged", "--limit", "200",
         "--json", "number,headRefName,headRefOid,mergeCommit,mergedAt"],
        cwd=root,
    )
    return prs or []


def fetch_deployments(root, slug, use_net):
    """GitHub Deployments API: {production: {...}|None, previews: [...]}.

    Vercel (and many providers) write deployment records with the exact SHA.
    We only trust a deployment whose newest status is success.
    """
    result = {"production": None, "previews": [], "available": False}
    if not use_net or not slug:
        return result
    deployments = gh_json(["api", f"repos/{slug}/deployments?per_page=30"], cwd=root)
    if not isinstance(deployments, list) or not deployments:
        return result
    result["available"] = True
    prod_candidates = []
    for d in deployments:
        env = (d.get("environment") or "").lower()
        entry = {"id": d.get("id"), "sha": d.get("sha"),
                 "environment": d.get("environment"), "created_at": d.get("created_at")}
        if "prod" in env:
            prod_candidates.append(entry)
        else:
            result["previews"].append(entry)
    if prod_candidates:
        newest = prod_candidates[0]  # API returns newest first
        statuses = gh_json(
            ["api", f"repos/{slug}/deployments/{newest['id']}/statuses?per_page=3"], cwd=root
        )
        if isinstance(statuses, list) and statuses:
            top = statuses[0]
            newest["status"] = top.get("state")
            newest["environment_url"] = top.get("environment_url")
            if top.get("state") == "success":
                result["production"] = newest
    return result


def fetch_deploy_runs(root, cfg, use_net):
    """Newest successful run per deploy-looking workflow, in ONE gh call."""
    if not use_net:
        return []
    runs = gh_json(
        ["run", "list", "--limit", "30",
         "--json", "workflowName,headSha,conclusion,status,updatedAt"],
        cwd=root,
    )
    if not runs:
        return []
    wanted = cfg.get("deploy_workflows")
    seen, picked = set(), []
    for r in runs:
        name = r.get("workflowName") or ""
        if wanted:
            match = name in wanted
        else:
            match = "deploy" in name.lower()
        if not match or r.get("conclusion") != "success" or name in seen:
            continue
        seen.add(name)
        picked.append({"workflow": name, "sha": r.get("headSha"), "updated": r.get("updatedAt")})
    return picked


def pr_ci_state(pr):
    """Summarize a PR's check rollup as one of: passing, failing, pending, none."""
    if not pr:
        return "none"
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        return "none"
    states = []
    for c in rollup:
        s = c.get("conclusion") or c.get("state") or ""
        states.append(s.upper())
    if any(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT") for s in states):
        return "failing"
    if any(s in ("PENDING", "IN_PROGRESS", "QUEUED", "") for s in states):
        return "pending"
    return "passing"


# --------------------------------------------------------------------------- #
# Deploy / live checks (all read-only HTTP GET)
# --------------------------------------------------------------------------- #
def http_get(url, timeout=7):
    # Only ever speak http(s). Config can come from a repo-local .is-it-live.json
    # in an untrusted clone, so file://, ftp://, data://, etc. must never reach
    # urlopen — otherwise a hostile config could read local files or hit internal
    # / cloud-metadata endpoints (SSRF) just by the user running the tool.
    try:
        scheme = urllib.parse.urlparse(url).scheme.lower()
    except Exception:  # noqa: BLE001
        return None, ""
    if scheme not in ("http", "https"):
        return None, ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"is-it-live/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(200_000).decode("utf-8", "replace")
            return resp.status, body
    except urllib.error.HTTPError as e:  # noqa: PERF203
        return e.code, ""
    except Exception:  # noqa: BLE001
        return None, ""


def detect_provider(root, cfg):
    if cfg.get("provider") and cfg["provider"] != "auto":
        return cfg["provider"]
    marker_files = ("vercel.json", os.path.join(".vercel", "project.json"))
    dirs = [root]
    try:
        dirs += [os.path.join(root, d) for d in sorted(os.listdir(root))
                 if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")][:40]
    except OSError:
        pass
    for d in dirs:
        for marker in marker_files:
            if os.path.exists(os.path.join(d, marker)):
                return "vercel"
    for marker, provider in (("netlify.toml", "netlify"), ("fly.toml", "fly"),
                             ("render.yaml", "render"), ("Procfile", "heroku")):
        if os.path.exists(os.path.join(root, marker)):
            return provider
    return "unknown"


def extract_json_path(body, json_path):
    """Pull a value out of a JSON body. Returns (value, error)."""
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None, "non-JSON response"
    val = data.get(json_path) if isinstance(data, dict) else None
    if val is None:
        return None, f"no '{json_path}' field in response"
    return str(val).strip(), None


def check_live(cfg, use_net, root):
    """Probe production URLs and every configured version source.

    Conservative by construction: a version SHA only counts when it looks like
    a hex commit id; bogus endpoint bodies are reported as errors, never as
    'the deployed commit'.
    """
    prod_urls = (cfg.get("production_urls") or [])[:MAX_PROBE_URLS]
    sources = version_sources(cfg)[:MAX_PROBE_URLS]
    # bundle_regex can come from an untrusted repo-local config. Compile it once
    # behind a guard so a bad pattern can't crash the run, and never let a
    # catastrophic-backtracking pattern loose on a large body.
    bundle_re = None
    if cfg.get("bundle_regex"):
        try:
            bundle_re = re.compile(cfg["bundle_regex"])
        except re.error:
            bundle_re = None
    result = {
        "checked": False,
        "network_skipped": not use_net,
        "configured_urls": list(prod_urls),
        "urls": [],          # [(url, status, ok)]
        "bundle": None,
        "version_results": [],  # [{label, url, sha_raw, sha_full, error}]
        "health": None,
        "notes": cfg.get("notes"),
    }
    if not use_net:
        return result
    if not prod_urls and not sources and not cfg.get("health_url"):
        return result

    result["checked"] = True
    for url in prod_urls:
        status, body = http_get(url)
        ok = status is not None and 200 <= status < 400
        result["urls"].append((url, status, ok))
        if ok and not result["bundle"] and bundle_re:
            m = bundle_re.search(body[:50_000])
            if m:
                result["bundle"] = m.group(0)

    for src in sources:
        entry = {"label": src["label"], "url": src["url"],
                 "sha_raw": None, "sha_full": None, "error": None}
        status, body = http_get(src["url"])
        if status is None:
            entry["error"] = "unreachable"
        elif not (200 <= status < 400):
            entry["error"] = f"HTTP {status}"
        elif not body.strip():
            entry["error"] = "empty response"
        else:
            sha = body.strip()
            if src.get("json_path"):
                sha, err = extract_json_path(body, src["json_path"])
                if err:
                    entry["error"] = err
                    result["version_results"].append(entry)
                    continue
            sha = (sha or "").strip().lower()
            if not HEX_SHA_RE.match(sha):
                entry["error"] = f"response is not a commit SHA ({sha[:24]!r})"
            else:
                entry["sha_raw"] = sha
                entry["sha_full"] = resolve_commit(root, sha)
                if not entry["sha_full"]:
                    entry["error"] = "deployed commit not found locally (run with --fetch)"
        result["version_results"].append(entry)

    health_url = cfg.get("health_url")
    if health_url:
        status, body = http_get(health_url)
        if status:
            result["health"] = {"status": status, "body": body[:300]}

    return result


def gather_proofs(live, deployments, deploy_runs, root):
    """Collect every evidence source that names a deployed commit.

    Returns a list of {label, source, sha_raw, sha_full, detail}; sha_full is
    set only when the commit exists locally (so ancestry checks are possible).
    """
    proofs = []
    for vr in live.get("version_results", []):
        if vr.get("sha_raw"):
            proofs.append({
                "label": vr["label"], "source": "version-endpoint",
                "sha_raw": vr["sha_raw"], "sha_full": vr["sha_full"],
                "detail": vr["url"],
            })
    prod = (deployments or {}).get("production")
    if prod and prod.get("sha"):
        proofs.append({
            "label": "github-deployment", "source": "github-deployment",
            "sha_raw": prod["sha"], "sha_full": resolve_commit(root, prod["sha"]),
            "detail": f"{prod.get('environment')} deployment, status {prod.get('status')}",
            "environment": prod.get("environment"),
        })
    for run_info in deploy_runs or []:
        if run_info.get("sha"):
            proofs.append({
                "label": f"workflow:{run_info['workflow']}", "source": "deploy-workflow",
                "sha_raw": run_info["sha"], "sha_full": resolve_commit(root, run_info["sha"]),
                "detail": f"successful run {run_info.get('updated', '')}",
            })
    return proofs


# --------------------------------------------------------------------------- #
# Classification: where does each recent commit sit?
# --------------------------------------------------------------------------- #
def build_squash_index(merged_prs, root):
    """{head_tip_sha -> pr_info} for squash-merge detection."""
    index = {}
    for pr in merged_prs or []:
        oid = pr.get("headRefOid")
        if not oid:
            continue
        merge_commit = (pr.get("mergeCommit") or {}).get("oid")
        index[oid] = {"number": pr.get("number"), "branch": pr.get("headRefName"),
                      "merge_commit": merge_commit, "merged_at": pr.get("mergedAt")}
    return index


def rev_list_set(root, ref, limit=300):
    """Set of commit SHAs reachable from ref (bounded). Cheap containment checks."""
    code, out, _ = git(["rev-list", f"-{limit}", ref], root)
    return set(out.split()) if code == 0 and out else set()


class SquashLookup:
    """Was this commit's content squash-merged? Cheap by construction:
    exact tip match is a dict hit; the ancestor case costs one rev-list per
    squashed tip that actually appears in HEAD's history (usually 0-2)."""

    def __init__(self, root, squash_index):
        self.root = root
        self.index = squash_index
        self._head_sets = None

    def for_commit(self, sha_full):
        info = self.index.get(sha_full)
        if info:
            return info
        if self._head_sets is None:
            self._head_sets = []
            head = rev_list_set(self.root, "HEAD")
            for tip, tip_info in self.index.items():
                if tip in head:
                    self._head_sets.append((rev_list_set(self.root, tip), tip_info))
        for commits, tip_info in self._head_sets:
            if sha_full in commits:
                return tip_info
        return None


class OpenPRLookup:
    """Which open PR contains a commit? One rev-list per locally-known PR head.

    PRs whose head SHA is not in local history (force-pushed elsewhere, not
    fetched) are tracked in `skipped` so callers can avoid asserting "no PR".
    """

    def __init__(self, root, open_prs):
        self.entries = []
        self.skipped = []
        for pr in open_prs:
            oid = pr.get("headRefOid")
            if not oid:
                continue
            if not resolve_commit(root, oid):
                self.skipped.append(pr)
                continue
            self.entries.append((rev_list_set(root, oid), pr))

    def for_commit(self, sha_full):
        for commits, pr in self.entries:
            if sha_full in commits:
                return pr
        return None


def match_component_paths(path, comp_paths):
    """Does a repo-relative file path belong to a component?

    Mirrors git pathspec semantics: literal prefix match FIRST (so a directory
    literally named "app/[id]" matches itself, as git would), then fnmatch for
    glob patterns. "." matches everything, like the git pathspec ".".
    """
    import fnmatch
    for p in comp_paths:
        p = (p or "").strip().rstrip("/")
        if not p:
            continue
        if p == ".":
            return True
        if path == p or path.startswith(p + "/"):
            return True
        if any(ch in p for ch in "*?[") and (
                fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path, p + "/*")):
            return True
    return False


def component_report(ctx, cfg, classified, staged, unstaged, untracked, limit):
    """Project-specific layer: where does each named component's newest change
    sit on the ladder? Components come from config:
      "components": [{"name": "...", "paths": ["dir/", "glob*"], "importance": "core"}]
    """
    comps = cfg.get("components") or []
    if not comps:
        return []
    root = ctx["root"]
    classified_by_sha = {c["full_sha"]: c for c in classified}

    # Files touched by the recent classified commits (one git call).
    # --no-renames lists both sides of a rename, matching pathspec behavior.
    touched = {}
    code, out, _ = git(["log", f"-n{max(1, limit)}", "--no-merges", "--no-renames",
                        "--format=%x01%H", "--name-only"], root)
    if code == 0 and out:
        cur = None
        for line in out.split("\n"):
            if line.startswith("\x01"):
                cur = line[1:].strip()
                touched[cur] = []
            elif line.strip() and cur:
                touched[cur].append(line.strip())

    dirty_all = set(staged) | set(unstaged) | set(untracked)
    results = []
    for comp in comps:
        if not isinstance(comp, dict):
            continue
        paths = [p for p in (comp.get("paths") or []) if p]
        entry = {
            "name": comp.get("name") or "unnamed",
            "importance": comp.get("importance"),
            "paths": paths,
            "last": None, "rung": None, "rung_label": None,
            "plain": None, "action": None,
            "dirty_files": 0, "recent_commits": 0,
        }
        if paths:
            entry["dirty_files"] = sum(
                1 for f in dirty_all if match_component_paths(f, paths))
            entry["recent_commits"] = sum(
                1 for files in touched.values()
                if any(match_component_paths(f, paths) for f in files))
            code, out, _ = git(
                ["log", "-1", "--no-merges", "--format=%h%x1f%H%x1f%s%x1f%cr", "--"] + paths,
                root)
            if code == 0 and out and "\x1f" in out:
                sha, full, subject, when = out.split("\x1f", 3)
                entry["last"] = {"sha": sha, "full_sha": full,
                                 "subject": subject, "when": when}
                pre = classified_by_sha.get(full)
                if pre:
                    rung, plain, action = pre["rung"], pre["plain"], pre["action"]
                else:
                    rung, plain, action = classify_commit(
                        ctx, {"sha": sha, "full_sha": full, "subject": subject})
                entry.update({"rung": rung, "rung_label": RUNGS[rung][0],
                              "plain": plain, "action": action})
        results.append(entry)
    return results


def surface_status(root, mref, main_sha, ancestry, proofs, live):
    """Per-production-surface status for the topology map.

    status: match (= tip of the deploy branch) | behind (live, lag N) |
    ahead | diverged | unresolved (sha not local) | up | down (URL-only).
    A reachable production URL whose host is already covered by a version-proof
    surface is folded into that proof (no contradictory sibling card).
    """
    from urllib.parse import urlparse
    surfaces = []
    proof_hosts = set()
    for p in proofs:
        is_url = p["source"] == "version-endpoint"
        s = {"label": p["label"], "source": p["source"],
             "url": p.get("detail") if is_url else None,
             "detail": p.get("detail"),
             "environment": p.get("environment"),
             "sha_raw": p["sha_raw"], "sha_full": p["sha_full"],
             "status": "unresolved", "lag": None}
        if is_url and p.get("detail"):
            try:
                proof_hosts.add(urlparse(p["detail"]).netloc)
            except ValueError:
                pass
        if p["sha_full"] and mref:
            if main_sha and p["sha_full"] == main_sha:
                s["status"] = "match"
            elif ancestry.is_ancestor(p["sha_full"], mref):
                s["status"] = "behind"
                out = git_ok(["rev-list", "--count", f"{p['sha_full']}..{mref}"], root)
                try:
                    s["lag"] = int(out) if out else None
                except ValueError:
                    pass
            elif main_sha and ancestry.is_ancestor(main_sha, p["sha_full"]):
                s["status"] = "ahead"
            else:
                s["status"] = "diverged"
        surfaces.append(s)
    for (url, status_code, ok) in live.get("urls", []):
        try:
            host = urlparse(url).netloc
        except ValueError:
            host = None
        if ok and host and host in proof_hosts:
            continue  # this domain's build is already proven above
        surfaces.append({"label": url, "source": "url", "url": url, "detail": None,
                         "environment": None, "sha_raw": None, "sha_full": None,
                         "status": "up" if ok else "down", "lag": None})
    return surfaces


def classify_commit(ctx, commit):
    """Returns (rung, plain-English status, next action) for one commit."""
    sha_full = commit["full_sha"]
    ancestry = ctx["ancestry"]
    mref = ctx["mref"]
    default = ctx["default"]
    live = ctx["live"]
    # Every proof stays in the denominator. A surface whose deployed SHA is not
    # in local history cannot be verified — that caps the rung at 7, it does
    # NOT silently shrink the bar for "LIVE".
    resolved = [p for p in ctx["proofs"] if p["sha_full"]]
    unresolved = [p for p in ctx["proofs"] if not p["sha_full"]]

    merged = ancestry.is_ancestor(sha_full, mref) if mref else False

    # --- Evidence-based rungs 7/8 --------------------------------------------
    if merged and resolved:
        containing = [p for p in resolved
                      if sha_full == p["sha_full"] or ancestry.is_ancestor(sha_full, p["sha_full"])]
        if len(containing) == len(resolved):
            srcs = ", ".join(sorted({p["label"] for p in containing}))
            if unresolved:
                pending = ", ".join(sorted({p["label"] for p in unresolved}))
                return 7, (f"deployed on {srcs}; {pending} reports "
                           f"{unresolved[0]['sha_raw'][:9]} which is not in local history"), \
                    "run with --fetch to verify the remaining surface"
            return 8, f"live — inside the deployed build (proof: {srcs})", "no action"
        if containing:
            have = ", ".join(sorted({p["label"] for p in containing}))
            missing = ", ".join(sorted({p["label"] for p in resolved if p not in containing}))
            return 7, f"deployed on {have}; not yet on {missing}", f"deploy/wait for {missing}"
        return 6, f"on {default}; newest deployed build does not include it yet", \
            f"deploy {default} (or wait for auto-deploy)"

    if merged:
        if unresolved:
            return 6, (f"on {default}; production reports "
                       f"{unresolved[0]['sha_raw'][:9]} which is not in local history"), \
                "re-run with --fetch so the deployed commit is recognized locally"
        if live.get("network_skipped") or not live.get("checked"):
            return 6, f"on {default}; live state NOT CHECKED (offline / no config)", \
                "re-run with network to verify live"
        urls = live.get("urls", [])
        if urls and not any(ok for (_, _, ok) in urls):
            return 6, f"on {default}, but production URL is unreachable", "check production health"
        version_errs = [v for v in live.get("version_results", []) if v.get("error")]
        if version_errs:
            e = version_errs[0]
            return 6, f"on {default}; version check failed ({e['label']}: {e['error']})", \
                "fix the version endpoint to make live provable"
        if urls:
            return 6, f"on {default}; production reachable but the exact build is unproven", \
                "add a /version endpoint to make 'live' provable"
        return 6, f"on {default}", f"deploy {default} (or check deploy)"

    # --- Not merged by ancestry: squash-merge content check -----------------
    squash = ctx["squash"].for_commit(sha_full)
    if squash:
        mc = resolve_commit(ctx["root"], squash.get("merge_commit"))
        if mc and resolved:
            containing = [p for p in resolved
                          if mc == p["sha_full"] or ancestry.is_ancestor(mc, p["sha_full"])]
            if len(containing) == len(resolved) and not unresolved:
                return 8, f"content live via squash-merge PR #{squash['number']} (this SHA differs)", \
                    "no action"
            if containing:
                have = ", ".join(sorted({p["label"] for p in containing}))
                return 7, (f"content deployed on {have} via squash PR #{squash['number']}; "
                           "remaining surfaces unverified"), \
                    "run with --fetch / wait for the remaining surface"
        return 6, f"content merged via squash PR #{squash['number']} (this SHA differs)", \
            "content is on main — branch can be cleaned up"

    # --- Pushed / PR / local -------------------------------------------------
    remotes = remote_refs_containing(ctx["root"], sha_full)
    if remotes:
        pr = ctx["pr_lookup"].for_commit(sha_full)
        if pr:
            ci = pr_ci_state(pr)
            n = pr["number"]
            if ci == "failing":
                return 5, f"in open PR #{n}, but CI is RED", f"fix CI on PR #{n}"
            if ci == "pending":
                return 5, f"in open PR #{n}, CI still running", f"wait for CI on PR #{n}"
            if pr.get("reviewDecision") == "APPROVED":
                return 5, f"in open PR #{n}, approved & green", f"merge PR #{n}"
            return 5, f"in open PR #{n}, awaiting review", f"get review on PR #{n}"
        if not ctx["use_net"]:
            return 4, "pushed to GitHub (branch); PR state not checked (offline)", \
                "re-run with network to check for a PR"
        if not ctx["gh_available"]:
            return 4, "pushed to GitHub (branch); PR state unknown (gh CLI not installed)", \
                f"open PR → {default}"
        if ctx["pr_lookup"].skipped:
            return 4, ("pushed to GitHub (branch); an open PR exists whose head "
                       "isn't in local history"), \
                "re-run with --fetch to check PR membership"
        return 4, "pushed to GitHub (branch), no open PR found", f"open PR → {default}"

    return 3, "committed on this machine only, not pushed", \
        f"push branch {ctx['branch']} to GitHub" if ctx["branch"] != "HEAD" \
        else "push to GitHub (detached HEAD — create a branch first)"


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #
def split_branches(root, mref, branches_meta, squash_index, ignore_prefixes):
    """Split not-in-main branches into truly-unmerged vs squash-merged, and
    active vs stale by last-commit age.

    Squash detection per branch: exact tip match against merged-PR head SHAs
    (the dominant case), plus a bounded rev-list scan so a branch whose tip has
    a few commits on top of a squash-merged head still counts as merged.
    """
    names_unmerged = unmerged_branch_names(root, mref)
    if names_unmerged is None:
        return None
    meta = {b["name"]: b for b in branches_meta}
    truly, squashed = [], []
    deep_scan_budget = 100
    for name in names_unmerged:
        b = meta.get(name) or {"name": name, "tip": None, "age_days": None}
        info = squash_index.get(b.get("tip"))
        if not info and b.get("tip") and deep_scan_budget > 0:
            deep_scan_budget -= 1
            recent = rev_list_set(root, b["tip"], limit=50)
            hits = [squash_index[t] for t in squash_index if t in recent]
            if hits:
                info = hits[0]
        if info:
            squashed.append({**b, "pr": info.get("number")})
        else:
            truly.append(b)
    active = [b for b in truly
              if b.get("age_days") is not None and b["age_days"] <= STALE_BRANCH_DAYS]
    stale = [b for b in truly if b not in active]
    visible = [b for b in truly
               if not any(b["name"].startswith(p) for p in ignore_prefixes)]
    notable = sorted(visible, key=lambda b: b.get("age_days") or 1e9)[:5]
    return {
        "total_local": len(branches_meta),
        "not_in_main": len(names_unmerged),
        "truly_unmerged": [b["name"] for b in truly],
        "squash_merged": [{"name": b["name"], "pr": b.get("pr")} for b in squashed],
        "active": [b["name"] for b in active],
        "stale": [b["name"] for b in stale],
        "notable": [b["name"] for b in notable],
    }


def build_report(root, cfg, limit, use_net, fetched):
    default = detect_default_branch(root, cfg.get("production_branch"))
    mref, _ = merged_ref(root, default)
    branch = current_branch(root)
    staged, unstaged, untracked, wt_detail = working_tree_status(root)
    ab = ahead_behind(root)
    commits = recent_commits(root, limit)
    branches_meta = local_branches_with_meta(root)
    trees = worktrees(root)
    ahead_main = commits_ahead_of_main(root, mref)
    provider = detect_provider(root, cfg)
    main_sha = head_sha_of(root, mref) if mref else None
    slug = origin_slug(root)
    gh_available = shutil.which("gh") is not None

    ancestry = AncestryCache(root)
    open_prs = fetch_open_prs(root, use_net and gh_available)
    merged = fetch_merged_prs(root, use_net and gh_available)
    squash_index = build_squash_index(merged, root)
    deployments = fetch_deployments(root, slug, use_net and gh_available)
    deploy_runs = fetch_deploy_runs(root, cfg, use_net and gh_available)
    live = check_live(cfg, use_net, root)
    proofs = gather_proofs(live, deployments, deploy_runs, root)
    sync = remote_sync_state(root, default, use_net)

    ignore_prefixes = cfg.get("ignore_branch_prefixes") or []
    branch_split = split_branches(root, mref, branches_meta, squash_index,
                                  ignore_prefixes) if mref else None

    ctx = {
        "root": root, "branch": branch, "default": default, "mref": mref,
        "use_net": use_net, "gh_available": gh_available,
        "ancestry": ancestry, "live": live, "proofs": proofs,
        "squash": SquashLookup(root, squash_index),
        "pr_lookup": OpenPRLookup(root, open_prs),
    }
    classified = []
    for c in commits:
        rung, plain, action = classify_commit(ctx, c)
        classified.append({**c, "rung": rung, "plain": plain, "action": action})

    components = component_report(ctx, cfg, classified, staged, unstaged, untracked, limit)
    surfaces = surface_status(root, mref, main_sha, ancestry, proofs, live)

    # Ladder counts: rungs 3-8 count commits; rungs 1-2 count FILES (labelled).
    counts = {r: 0 for r in RUNGS}
    for c in classified:
        counts[c["rung"]] += 1
    staged_set, unstaged_set = set(staged), set(unstaged)
    counts[2] += len(staged_set)
    counts[1] += len((unstaged_set - staged_set)) + len(untracked)

    # Deployment lag: how far behind main is the newest production proof?
    deploy_lag = None
    resolved = [p for p in proofs if p["sha_full"]]
    if resolved and mref:
        p = resolved[0]
        out = git_ok(["rev-list", "--count", f"{p['sha_full']}..{mref}"], root)
        try:
            deploy_lag = int(out) if out is not None else None
        except ValueError:
            deploy_lag = None

    # Confidence: how much can we actually claim about rungs 7-8?
    unresolved = [p for p in proofs if not p["sha_full"]]
    version_responded = any(
        vr.get("sha_raw") or
        (vr.get("error") and vr["error"] != "unreachable" and not vr["error"].startswith("HTTP"))
        for vr in live.get("version_results", [])
    )
    if resolved and not unresolved:
        confidence = "verified"
    elif proofs:
        # at least one surface named a deployed commit we can't resolve locally
        confidence = "proof_unresolved"
    elif live.get("network_skipped") or not live.get("checked"):
        confidence = "not_checked"
    elif any(ok for (_, _, ok) in live.get("urls", [])) or version_responded:
        confidence = "reachable_unverified"
    else:
        confidence = "unreachable"

    rep = {
        "schema_version": 2,
        "tool_version": __version__,
        "root": root,
        "branch": branch,
        "detached": branch == "HEAD",
        "default": default,
        "mref": mref,
        "provider": provider,
        "slug": slug,
        "gh_available": gh_available,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "wt_detail": wt_detail,
        "ahead_behind": ab,
        "ahead_main": ahead_main,
        "branch_split": branch_split,
        "worktrees": trees,
        "classified": classified,
        "components": components,
        "surfaces": surfaces,
        "counts": counts,
        "open_prs": [{"number": p.get("number"), "title": p.get("title"),
                      "branch": p.get("headRefName"), "ci": pr_ci_state(p)} for p in open_prs],
        "live": live,
        "proofs": proofs,
        "deployments": deployments,
        "deploy_runs": deploy_runs,
        "deploy_lag": deploy_lag,
        "main_sha": main_sha,
        "sync": sync,
        "confidence": confidence,
        "offline": not use_net,
        "fetched": fetched,
        "generated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "generated_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rep["verdict"] = build_verdict(rep)
    rep["state_digest"] = state_digest(rep)
    return rep


def state_digest(rep):
    """Hash of the report CONTENT (no timestamps), so the live dashboard can
    reload only when the picture actually changed."""
    import hashlib
    stable = {
        "branch": rep["branch"],
        "staged": rep["staged"], "unstaged": rep["unstaged"], "untracked": rep["untracked"],
        "classified": [(c["sha"], c["rung"], c["plain"], c["action"]) for c in rep["classified"]],
        "surfaces": [(s["label"], s["status"], s["lag"], s["sha_raw"]) for s in rep["surfaces"]],
        "components": [(c["name"], c["rung"], c["dirty_files"], c["recent_commits"],
                        (c["last"] or {}).get("sha")) for c in rep["components"]],
        "verdict": rep["verdict"]["verdict"],
        "confidence": rep["confidence"],
        "headline": rep["verdict"]["headline"],
        "problems": rep["verdict"]["problems"],
        "next": rep["verdict"]["next_action"]["text"],
        "sync": rep["sync"]["state"],
        "counts": rep["counts"],
        "ahead_behind": rep["ahead_behind"],
        "open_prs": rep["open_prs"],
    }
    return hashlib.sha1(
        json.dumps(stable, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def newest_rung(rep):
    return rep["classified"][0]["rung"] if rep["classified"] else None


def gap_banner(rep):
    """The headline. Tri-state honest: live / not-live / NOT CHECKED.

    kind: ok (green) | info (amber, normal in-flight state) | warn (red, something
    is actually wrong).
    """
    rc = rep["classified"]
    if not rc:
        return None
    newest = rc[0]
    n = len(rc)
    local_paths = set(rep["staged"] + rep["unstaged"] + rep["untracked"])
    confidence = rep["confidence"]

    if newest["rung"] >= 8:
        if local_paths:
            return ("info",
                    f"Newest commit is LIVE ✓ but {plural(len(local_paths), 'local file')} "
                    "are still unsaved (working tree/staged)")
        return ("ok", f"Recent work is LIVE ✓ (newest of {n} commits is in the deployed build)")

    if confidence == "not_checked" and newest["rung"] >= 6:
        label = RUNGS[newest["rung"]][0]
        return ("info",
                f"Newest work is at rung {newest['rung']} ({label}); live state NOT CHECKED "
                "(offline or no config) — this run cannot say whether it is live")

    if confidence == "proof_unresolved" and newest["rung"] >= 6:
        return ("info",
                "Production reports a commit that is not in local history — "
                "run with --fetch for a verified answer")

    if newest["rung"] == 7:
        return ("info", "Newest work is DEPLOYED but not yet on every production surface")
    if newest["rung"] in (5, 6):
        verb = RUNGS[newest["rung"]][0]
        if rep.get("deploy_lag"):
            return ("info",
                    f"Newest work is {verb}; production is {plural(rep['deploy_lag'], 'commit')} "
                    f"behind {rep['default']}")
        if confidence == "verified":
            return ("info", f"Newest work is {verb}, not yet in the deployed build")
        return ("info", f"Newest work is {verb}; live build unverified (no version proof)")
    if newest["rung"] <= 4:
        verb = RUNGS[newest["rung"]][0]
        return ("info", f"GAP: newest work is only at rung {newest['rung']} ({verb}) — "
                        f"it has not reached {rep['default']}, so it cannot be live")
    return None


def something_is_wrong(rep):
    """Red-banner conditions: things that are actually BAD, not merely in-flight."""
    problems = []
    for p in rep["open_prs"]:
        if p["ci"] == "failing":
            problems.append(f"CI is RED on PR #{p['number']}")
    for (url, status, ok) in rep["live"].get("urls", []):
        if not ok:
            problems.append(f"production URL unreachable: {url} (HTTP {status})")
    # Note: version-endpoint errors are surfaced in the LIVE NOW panel, not here —
    # a bad endpoint is not by itself a red-banner event.
    anc = AncestryCache(rep["root"])
    for p in rep["proofs"]:
        if p["sha_full"] and rep["mref"]:
            # divergence: deployed commit is NOT on main at all
            if not anc.is_ancestor(p["sha_full"], rep["mref"]) and \
               not anc.is_ancestor(rep["main_sha"] or "", p["sha_full"]):
                problems.append(
                    f"deployed commit {p['sha_raw'][:9]} ({p['label']}) is NOT on {rep['default']} — divergence")
    return problems


def recommended_next_action(rep):
    """One action with a concrete object. Returns {kind, target, text}."""
    rc = rep["classified"]
    if not rc:
        local = rep["staged"] or rep["unstaged"] or rep["untracked"]
        if local:
            return {"kind": "commit", "target": None,
                    "text": "no commits yet — commit the local changes to start climbing",
                    "done": False}
        return {"kind": "none", "target": None,
                "text": "no commits found to classify", "done": True}
    newest = rc[0] if rc else None
    if newest and newest["rung"] < 8:
        kind = {3: "push", 4: "open_pr", 5: "pr", 6: "deploy", 7: "deploy"}.get(newest["rung"], "other")
        return {"kind": kind, "target": newest["sha"], "text": newest["action"], "done": False}
    local_paths = set(rep["staged"] + rep["unstaged"] + rep["untracked"])
    if local_paths:
        return {"kind": "commit", "target": None,
                "text": "commit, stash, or discard the local working-tree changes", "done": False}
    for cm in rc:
        if cm["rung"] < 8:
            return {"kind": "other", "target": cm["sha"], "text": cm["action"], "done": False}
    return {"kind": "none", "target": None,
            "text": "nothing to do — newest work is verified live", "done": True}


def build_verdict(rep):
    nr = newest_rung(rep)
    if nr is None:
        verdict = "empty"
    elif rep["confidence"] == "not_checked" and nr >= 6:
        verdict = "unknown"
    elif nr >= 8:
        verdict = "live"
    elif nr >= 5:
        verdict = "in_flight"
    else:
        verdict = "not_shipped"
    banner = gap_banner(rep)
    action = recommended_next_action(rep)
    problems = something_is_wrong(rep)
    return {
        "verdict": verdict,
        "confidence": rep["confidence"],
        "headline": banner[1] if banner else None,
        "banner_kind": "warn" if problems else (banner[0] if banner else None),
        "problems": problems,
        "next_action": action,
        "newest": ({"sha": rep["classified"][0]["sha"],
                    "subject": rep["classified"][0]["subject"],
                    "rung": rep["classified"][0]["rung"],
                    "rung_label": RUNGS[rep["classified"][0]["rung"]][0],
                    "action": rep["classified"][0]["action"]}
                   if rep["classified"] else None),
    }


# --------------------------------------------------------------------------- #
# Terminal rendering
# --------------------------------------------------------------------------- #
def short(s, n):
    return s if len(s) <= n else s[: max(1, n - 1)] + "…"


def term_width():
    try:
        cols = shutil.get_terminal_size((100, 24)).columns
    except Exception:  # noqa: BLE001
        cols = 100
    return max(80, min(cols, 140))


def render_terminal(rep, color=False):
    def c(code, s):
        return f"\033[{code}m{s}\033[0m" if color else s

    out = []
    W = term_width()
    d = rep["wt_detail"]
    if not (rep["staged"] or rep["unstaged"] or rep["untracked"]):
        wt = "CLEAN ✓"
    else:
        bits = []
        if rep["staged"]:
            bits.append(f"{len(rep['staged'])} staged")
        if d["modified"]:
            bits.append(f"{d['modified']} modified")
        if d["deleted"]:
            bits.append(f"{d['deleted']} deleted")
        if rep["untracked"]:
            bits.append(f"{len(rep['untracked'])} untracked")
        wt = ", ".join(bits)
    ab = rep["ahead_behind"]
    if rep["detached"]:
        branch_txt = "detached HEAD ⚠ (no branch label — easy to lose)"
    else:
        branch_txt = rep["branch"]
    if ab is None:
        abs_txt = "this branch isn't on GitHub yet (no upstream)"
    elif ab == (0, 0):
        abs_txt = "in sync with its GitHub copy ✓"
    else:
        abs_txt = f"ahead {ab[0]}, behind {ab[1]} vs its GitHub copy"

    out.append("═" * W)
    mode = "OFFLINE SNAPSHOT — rungs 7-8 not checked" if rep["offline"] else "online"
    out.append(c("1", f"  is-it-live · {os.path.basename(rep['root'])}")
               + f"        provider: {rep['provider']} · {mode}")
    out.append(f"  branch:       {short(branch_txt, W - 18)}")
    out.append(f"  working tree: {wt}")
    out.append(f"  vs GitHub:    {abs_txt}")
    out.append(f"  GitHub check: {rep['sync']['label']}")
    out.append("═" * W)
    out.append("")

    v = rep["verdict"]
    if v["problems"]:
        for p in v["problems"]:
            out.append(c("1;31", f"  ✗  PROBLEM: {p}"))
        out.append("")
    banner = gap_banner(rep)
    if banner:
        kind, msg = banner
        sym = {"ok": "✓", "info": "→", "warn": "⚠"}.get(kind, "→")
        code = {"ok": "1;32", "info": "1;33", "warn": "1;31"}.get(kind, "1")
        out.append(c(code, f"  {sym}  {msg}"))
        if kind != "ok" and rep["mref"] and rep["confidence"] != "not_checked":
            out.append(f"     production deploys from `{rep['default']}`; "
                       f"climbing the ladder = getting onto {rep['default']} and deployed.")
        out.append("")

    # The ladder
    nr = newest_rung(rep)
    out.append("  THE LADDER")
    out.append("  " + "─" * (W - 4))
    maxc = max([1] + list(rep["counts"].values()))
    for r in range(8, 0, -1):
        label, meaning = RUNGS[r]
        n = rep["counts"][r]
        unit = "file" if r <= 2 else "commit"
        barlen = max(1, int((n / maxc) * 18)) if n else 0
        bar = "█" * barlen if n else "·"
        marker = "  ← newest work is HERE" if (r == nr) else ""
        left = f"  {r}  {label:<13} {meaning}"
        left = short(left, 54).ljust(54)
        out.append(f"{left} {bar} ({plural(n, unit)}){marker}")
    out.append("")

    # Recent work: still-climbing first, already-live collapsed
    climbing = [cm for cm in rep["classified"] if cm["rung"] < 8]
    done = [cm for cm in rep["classified"] if cm["rung"] >= 8]
    out.append("  STILL CLIMBING — newest first")
    out.append("  " + "─" * (W - 4))
    shown = climbing[:12]
    for cm in shown:
        label = RUNGS[cm["rung"]][0]
        tail = f" [{cm['rung']}] {label:<10} ▶ {cm['action']}"
        subj_w = max(12, W - 14 - len(tail))
        out.append(f"   {cm['sha']:<9} {short(cm['subject'], subj_w):<{subj_w}}{tail}")
    if len(climbing) > len(shown):
        out.append(f"   … and {len(climbing) - len(shown)} more still climbing (see --json for all)")
    if not climbing:
        out.append("   (nothing climbing — every recent commit is live or there are no commits)")
    if done:
        out.append(f"   ✓ {plural(len(done), 'older commit')} already verified live")
    out.append("")

    # Components (project-specific layer, only when configured)
    if rep["components"]:
        out.append("  COMPONENTS — the parts of this project, and how live each one is")
        out.append("  " + "─" * (W - 4))
        for comp in rep["components"]:
            if not comp["last"]:
                out.append(f"   ·  {comp['name']:<28} (no commits touch its paths)")
                continue
            sym = "✓" if comp["rung"] == 8 else "→"
            imp = " [" + comp["importance"] + "]" if comp.get("importance") else ""
            line = (f"   {sym}  {short(comp['name'], 28):<28} {comp['rung_label']:<10} "
                    f"last change {comp['last']['sha']} {comp['last']['when']}")
            out.append(short(line, W))
            extras = []
            if comp["dirty_files"]:
                extras.append(f"{plural(comp['dirty_files'], 'uncommitted file')} ⚠")
            if comp["rung"] is not None and comp["rung"] < 8 and comp["action"] != "no action":
                extras.append(f"▶ {comp['action']}")
            if imp:
                extras.append(imp.strip())
            if extras:
                out.append(short("        " + "  ·  ".join(extras), W))
        out.append("")

    # Parked off the ladder
    out.append("  PARKED OFF THE LADDER")
    out.append("  " + "─" * (W - 4))
    bs = rep["branch_split"]
    if bs:
        if bs["squash_merged"]:
            out.append(f"   {len(bs['truly_unmerged'])} branches truly not in {rep['default']} · "
                       f"{len(bs['squash_merged'])} look unmerged but were squash-merged "
                       "(safe to clean up)")
        else:
            out.append(f"   {bs['not_in_main']} / {bs['total_local']} local branches not in {rep['default']}")
        if bs["truly_unmerged"]:
            out.append(f"   of the truly unmerged: {len(bs['active'])} active (last {STALE_BRANCH_DAYS}d), "
                       f"{len(bs['stale'])} stale (probably abandoned)")
        if bs["notable"]:
            out.append("   most recent unmerged: " + short("  ·  ".join(bs["notable"]), W - 26))
    if rep["ahead_main"]:
        out.append(f"   this branch is {plural(rep['ahead_main'], 'commit')} ahead of {rep['default']}")
    trees = rep["worktrees"]
    if len(trees) > 1:
        scratch = [t for t in trees if t.get("agent_scratch")]
        manual_detached = [t for t in trees if t.get("detached") and not t.get("agent_scratch")]
        line = f"   {len(trees)} worktrees"
        if scratch:
            line += f" ({len(scratch)} agent-scratch)"
        out.append(line)
        if manual_detached:
            out.append(f"   ⚠ {plural(len(manual_detached), 'hand-made worktree')} in detached HEAD "
                       "(no branch label — at risk of being lost)")
    out.append("")

    # Live panel
    out.append("  LIVE NOW")
    out.append("  " + "─" * (W - 4))
    live = rep["live"]
    if rep["offline"]:
        if live.get("configured_urls"):
            out.append("   network checks skipped (--no-net); production URLs not probed:")
            for url in live["configured_urls"]:
                out.append(f"   - {url}")
        else:
            out.append("   offline run — rungs 7-8 were not checked.")
    elif not live.get("checked") and not rep["proofs"]:
        out.append("   no production URL configured for this repo.")
        out.append("   quickest fix: run with --init to scaffold a .is-it-live.json here.")
        out.append("   (the local ladder above is still fully accurate; config only adds rungs 7-8)")
    else:
        for (url, status, ok) in live.get("urls", []):
            mark = "✓" if ok else "✗"
            out.append(f"   {mark} {url}  (HTTP {status})")
        if live.get("bundle"):
            out.append(f"   served build asset: {live['bundle']}")
        for vr in live.get("version_results", []):
            if vr.get("sha_raw") and not vr.get("error"):
                tip = " = tip of " + rep["default"] + " ✓" \
                    if rep["main_sha"] and rep["main_sha"].startswith(vr["sha_raw"][:9]) else ""
                out.append(f"   {vr['label']}: deployed commit {vr['sha_raw'][:12]}{tip}")
            elif vr.get("sha_raw"):
                out.append(f"   {vr['label']}: reports {vr['sha_raw'][:12]} ⚠ {vr['error']}")
            else:
                out.append(f"   {vr['label']}: version check failed — {vr['error']}")
        prod = (rep["deployments"] or {}).get("production")
        if prod:
            out.append(f"   GitHub deployment: {prod['sha'][:12]} → {prod.get('environment')} "
                       f"({prod.get('status')})")
        for r_info in rep["deploy_runs"]:
            out.append(f"   deploy workflow '{r_info['workflow']}': success @ {r_info['sha'][:12]}")
        if rep["deploy_lag"]:
            out.append(f"   production is {plural(rep['deploy_lag'], 'commit')} behind {rep['default']}")
        elif rep["confidence"] == "verified":
            out.append(f"   production matches the tip of {rep['default']} ✓")
        if not rep["proofs"] and live.get("checked"):
            out.append("   exact deployed commit: UNVERIFIED (no version endpoint / deployment records)")
        if live.get("health"):
            out.append(f"   health: HTTP {live['health']['status']}  {short(live['health']['body'], 50)}")
    if not rep["gh_available"] and rep["slug"]:
        out.append("   gh CLI not found — PR status, squash detection and deployment proof are off.")
        out.append("   install it (brew install gh) for the full picture.")
    if live.get("notes"):
        out.append(f"   note: {live['notes']}")
    out.append("")

    # One next action
    action = rep["verdict"]["next_action"]
    if action["done"]:
        out.append(c("1;36", f"  ▶ ONE NEXT ACTION:  {action['text']}."))
    else:
        out.append(c("1;36", f"  ▶ ONE NEXT ACTION:  {action['text']},"))
        out.append(c("1;36", "                      then re-run is-it-live to confirm it climbed."))
    out.append("")
    out.append(f"  read-only · is-it-live v{__version__} · generated {rep['generated']}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #
RUNG_COLORS = {
    8: "#22c55e", 7: "#eab308", 6: "#06b6d4", 5: "#14b8a6",
    4: "#3b82f6", 3: "#a855f7", 2: "#ec4899", 1: "#ef4444",
}


def fix_prompt(rep, subject, sha, rung, plain, action):
    return (f"In repo {rep['root']}: \"{subject}\" ({sha}) is at rung {rung} "
            f"({RUNGS[rung][0]}) — {plain}. Next action: {action}. "
            "Please do this, then re-run is-it-live to confirm it climbed.")


# Plain string (not an f-string) so JS braces need no escaping.
JOURNEY_JS = r"""
(function(){
var NS='http://www.w3.org/2000/svg';
var svg=document.getElementById('jsvg');
if(!svg)return;
function el(t,attrs,parent){var e=document.createElementNS(NS,t);for(var k in attrs)e.setAttribute(k,attrs[k]);(parent||svg).appendChild(e);return e;}
function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':s);return d.innerHTML;}

var defs=el('defs',{});
var grads={};
function grad(color){
  var id='bg'+color.replace('#','');
  if(!grads[id]){
    var g=el('radialGradient',{id:id,cx:'38%',cy:'34%',r:'80%'},defs);
    el('stop',{offset:'0%','stop-color':color,'stop-opacity':'0.6'},g);
    el('stop',{offset:'60%','stop-color':color,'stop-opacity':'0.2'},g);
    el('stop',{offset:'100%','stop-color':color,'stop-opacity':'0.06'},g);
    grads[id]=1;
  }
  return 'url(#'+id+')';
}
var zg=el('radialGradient',{id:'zoneg',cx:'50%',cy:'42%',r:'78%'},defs);
el('stop',{offset:'0%','stop-color':'#22c55e','stop-opacity':'0.14'},zg);
el('stop',{offset:'100%','stop-color':'#22c55e','stop-opacity':'0.02'},zg);
var fl=el('filter',{id:'glow',x:'-60%',y:'-60%',width:'220%',height:'220%'},defs);
el('feGaussianBlur',{stdDeviation:'5',result:'b'},fl);
var mg=el('feMerge',{},fl);el('feMergeNode',{'in':'b'},mg);el('feMergeNode',{'in':'SourceGraphic'},mg);

var ST={laptop:{x:110,label:'YOUR LAPTOP'},github:{x:380,label:'GITHUB'},main:{x:620,label:'MAIN'},live:{x:868}};
var Y=312;
var minY=56;

el('rect',{x:752,y:56,width:236,height:Y+34-56,rx:20,fill:'url(#zoneg)',
  stroke:'#1e4d2b','stroke-width':1.5,'class':'zone'});
el('text',{x:870,y:Y+58,'class':'stlabel','text-anchor':'middle'}).textContent='LIVE — users see this';

var segs=[[ST.laptop.x+30,ST.github.x-30],[ST.github.x+30,ST.main.x-30],[ST.main.x+30,752]];
segs.forEach(function(sg,i){
  el('line',{x1:sg[0],y1:Y,x2:sg[1],y2:Y,stroke:'#1f2737','stroke-width':3,'stroke-linecap':'round'});
  el('line',{x1:sg[0],y1:Y,x2:sg[1],y2:Y,stroke:J.edges[i],'stroke-width':3,
    'stroke-linecap':'round',id:'seg'+i,opacity:'0.22'});
  for(var pi=0;pi<2;pi++){
    var p=el('circle',{r:2.6,fill:J.edges[i],opacity:'0.9'});
    el('animateMotion',{dur:'2.8s',begin:(pi*1.4)+'s',repeatCount:'indefinite',
      path:'M'+sg[0]+','+Y+' L'+sg[1]+','+Y},p);
  }
});

['laptop','github','main'].forEach(function(k){
  var s=ST[k];
  el('circle',{cx:s.x,cy:Y,r:24,fill:'#11151f',stroke:'#2b3347','stroke-width':1.5});
  el('circle',{cx:s.x,cy:Y,r:6,fill:(J.stations&&J.stations[k])||'#3a4256'});
  el('text',{x:s.x,y:Y+58,'class':'stlabel','text-anchor':'middle'}).textContent=s.label;
});

var byStation={};
J.travelers.forEach(function(t){(byStation[t.station]=byStation[t.station]||[]).push(t);});
var panel=document.getElementById('jpanel');
function select(t,g){
  var sel=document.querySelectorAll('.bubble.sel');
  for(var i=0;i<sel.length;i++){
    sel[i].classList.remove('sel');
    var c=sel[i].querySelector('circle');if(c)c.removeAttribute('filter');
  }
  var old=document.getElementById('selring');if(old)old.parentNode.removeChild(old);
  if(g){
    g.classList.add('sel');
    var mc=g.querySelector('circle');if(mc)mc.setAttribute('filter','url(#glow)');
    var ring=el('circle',{id:'selring',r:t.r+4,fill:'none',stroke:t.color,
      'stroke-width':1.4,opacity:'0.8'},g);
    el('animate',{attributeName:'r',from:(t.r+3),to:(t.r+11),dur:'1.8s',repeatCount:'indefinite'},ring);
    el('animate',{attributeName:'opacity',from:'0.7',to:'0',dur:'1.8s',repeatCount:'indefinite'},ring);
  }
  var html='<div class=pname>'+esc(t.label)+(t.importance?' <span class=imp>'+esc(t.importance)+'</span>':'')+'</div>';
  t.story.forEach(function(s){html+='<div class=pline>'+esc(s)+'</div>';});
  if(t.action){html+='<div class=pact>&#9654; '+esc(t.action)+
    ' <button class=copy data-prompt="'+esc(t.prompt).replace(/"/g,'&quot;')+'">copy fix prompt</button></div>';}
  panel.innerHTML=html;
  var idx={laptop:0,github:1,main:2,live:3}[t.station];
  for(var i2=0;i2<3;i2++){
    var sg2=document.getElementById('seg'+i2);
    if(sg2)sg2.setAttribute('opacity',i2<idx?'0.95':'0.22');
  }
}
Object.keys(byStation).forEach(function(k){
  byStation[k].forEach(function(t,i){
    var col=i%2,row=Math.floor(i/2);
    var x,y;
    if(k==='live'){x=812+col*98;y=Y-92-row*68;}
    else{x=ST[k].x-28+col*64;y=Y-86-row*68;}
    if(y-t.r<minY)minY=y-t.r;
    var g=el('g',{'class':'bubble','data-id':t.id,transform:'translate('+x+','+y+')'});
    el('circle',{r:t.r,fill:grad(t.color),stroke:t.color,'stroke-width':1.8},g);
    if(t.station==='live'&&t.rung===8){el('text',{'class':'check','text-anchor':'middle',dy:'4'},g).textContent='✓';}
    var ly=(col===1)?-(t.r+9):(t.r+16);
    if(y+ly-12<minY)minY=y+ly-12;
    var lbl=el('text',{'class':'blabel','text-anchor':'middle',y:ly},g);
    lbl.textContent=t.label.length>15?t.label.slice(0,14)+'…':t.label;
    g.addEventListener('click',function(){select(t,g);});
  });
});
svg.setAttribute('viewBox','16 '+(minY-22)+' 984 '+(Y+74-(minY-22)));
var init=null;
J.travelers.forEach(function(t){if(!init&&t.station!=='live')init=t;});
if(!init)init=J.travelers[0];
if(init){var g0=document.querySelector('.bubble[data-id="'+init.id+'"]');select(init,g0);}
})();
"""


def render_html(rep, live_mode=False, interval=60):
    import html as _html

    def esc(s):
        return _html.escape(str(s))

    v = rep["verdict"]

    # ---- Hero: ONE huge answer, everything else is subordinate -------------
    HERO = {
        "live": ("#22c55e", "Everything you shipped is live."),
        "in_flight": ("#eab308", "Your newest work is on its way — not live yet."),
        "not_shipped": ("#eab308", "Your newest work hasn't reached production yet."),
        "unknown": ("#94a3b8", "Can't tell what's live from this run."),
        "empty": ("#94a3b8", "No commits here yet."),
    }
    hero_col, hero_text = HERO.get(v["verdict"], ("#94a3b8", str(v["verdict"])))
    if v["problems"]:
        hero_col, hero_text = "#ef4444", "Something needs attention."
    problems_html = "".join(f'<div class=problem>✗ {esc(p)}</div>' for p in v["problems"])
    hsub = ""
    if v["headline"] and v["headline"].rstrip(".") != hero_text.rstrip("."):
        hsub = f'<div class=hsub>{esc(v["headline"])}</div>'
    CONF_PHRASE = {
        "verified": "proven by " + plural(len([p for p in rep["proofs"] if p["sha_full"]]), "source"),
        "proof_unresolved": "run --fetch for a verified answer",
        "reachable_unverified": "production reachable, build unproven",
        "not_checked": "live state not checked this run",
        "unreachable": "production did not respond",
    }
    live_badge = (f' · <span class=pulse>● live — refreshes every {interval}s</span>'
                  if live_mode else "")
    hero_html = (
        f'<div class=hero><span class=bigdot style="background:{hero_col};'
        f'box-shadow:0 0 26px {hero_col}66"></span>'
        f'<div><div class=htext style="color:{hero_col}">{esc(hero_text)}</div>'
        f'{hsub}{problems_html}'
        f'<div class=hmeta>{esc(os.path.basename(rep["root"]))} · branch {esc(rep["branch"])}'
        f' · checked {esc(rep["generated"])} · {esc(CONF_PHRASE.get(rep["confidence"], ""))}'
        f'{live_badge}</div></div></div>')

    action = v["next_action"]
    action_html = ""
    if not action["done"]:
        btn = ""
        if rep["classified"] and rep["classified"][0]["rung"] < 8:
            cm0 = rep["classified"][0]
            prompt0 = fix_prompt(rep, cm0["subject"], cm0["sha"], cm0["rung"], cm0["plain"], cm0["action"])
            btn = f' <button class=copy data-prompt="{esc(prompt0)}">copy fix prompt</button>'
        action_html = (f'<div class="action">▶ ONE NEXT ACTION&nbsp; <b>{esc(action["text"])}</b>'
                       f'{btn}</div>')

    # --- The Journey: work travels Your Laptop -> GitHub -> Main -> LIVE ----
    def dot(color, size=9):
        return (f'<span class=dot style="background:{color};width:{size}px;'
                f'height:{size}px"></span>')

    STATION_FOR_RUNG = {1: "laptop", 2: "laptop", 3: "laptop",
                        4: "github", 5: "github", 6: "main", 7: "live", 8: "live"}
    HUMAN_STATUS = {
        8: "Live ✓ — users have this right now.",
        7: "Deployed, but not on every production surface yet.",
        6: "Merged into main — approved and packed, waiting on the deploy step.",
        5: "On GitHub as a pull request — waiting for review and checks.",
        4: "On GitHub, but on a side branch — it needs a pull request.",
        3: "Saved, but only on this machine.",
        2: "Marked to be saved, not saved yet.",
        1: "Raw edits — saved nowhere.",
    }

    travelers = []
    n_local = len(rep["staged"]) + len(rep["unstaged"]) + len(rep["untracked"])
    if n_local:
        sample = (rep["unstaged"] + rep["untracked"] + rep["staged"])[:3]
        travelers.append({
            "id": "unsaved", "label": f"{n_local} unsaved files", "importance": None,
            "rung": 1, "station": "laptop", "color": "#eab308", "r": 21,
            "story": [
                "Raw edits in this checkout — they exist only in the editor, saved nowhere.",
                "If this machine died right now, these changes would be gone.",
                "Including: " + ", ".join(sample) + ("…" if n_local > 3 else ""),
            ],
            "action": "commit, stash, or discard them",
            "prompt": (f"In repo {rep['root']}: there are {n_local} unsaved working-tree "
                       "files. Review them, then commit, stash, or discard so nothing is lost."),
        })
    if rep["components"]:
        for ci, comp in enumerate(rep["components"]):
            if not comp["last"]:
                continue
            story = [HUMAN_STATUS.get(comp["rung"], comp["plain"] or "")]
            if comp["plain"] and comp["rung"] != 8:
                story.append(f"Detail: {comp['plain']}.")
            story.append(f"Last change: “{short(comp['last']['subject'], 70)}” "
                         f"({comp['last']['sha']}) · {comp['last']['when']} · "
                         f"{plural(comp['recent_commits'], 'recent commit')}.")
            if comp["dirty_files"]:
                story.append(f"⚠ {plural(comp['dirty_files'], 'file')} in this part "
                             "edited but unsaved.")
            travelers.append({
                "id": f"comp{ci}", "label": comp["name"], "importance": comp.get("importance"),
                "rung": comp["rung"], "station": STATION_FOR_RUNG[comp["rung"]],
                "color": RUNG_COLORS[comp["rung"]],
                "r": 26 if comp.get("importance") else 21,
                "story": story,
                "action": comp["action"] if (comp["rung"] < 8 and comp["action"] != "no action") else None,
                "prompt": fix_prompt(rep, f'component {comp["name"]}: {comp["last"]["subject"]}',
                                     comp["last"]["sha"], comp["rung"], comp["plain"], comp["action"]),
            })
    else:
        for ci, cm in enumerate(rep["classified"][:8]):
            travelers.append({
                "id": f"c{ci}", "label": short(cm["subject"], 24), "importance": None,
                "rung": cm["rung"], "station": STATION_FOR_RUNG[cm["rung"]],
                "color": RUNG_COLORS[cm["rung"]], "r": 18,
                "story": [HUMAN_STATUS.get(cm["rung"], ""), f"Detail: {cm['plain']}.",
                          f"Commit {cm['sha']}."],
                "action": cm["action"] if (cm["rung"] < 8 and cm["action"] != "no action") else None,
                "prompt": fix_prompt(rep, cm["subject"], cm["sha"], cm["rung"],
                                     cm["plain"], cm["action"]),
            })

    EDGE1 = {"in_sync": "#22c55e", "behind": "#eab308"}
    EDGE2 = {"verified": "#22c55e", "proof_unresolved": "#eab308",
             "unreachable": "#ef4444"}
    journey = {
        "travelers": travelers,
        "edges": [EDGE1.get(rep["sync"]["state"], "#3a4256"),
                  "#3a4256",
                  EDGE2.get(rep["confidence"], "#3a4256")],
        "stations": {
            "laptop": "#eab308" if (n_local or rep["detached"]) else "#22c55e",
            "github": EDGE1.get(rep["sync"]["state"], "#3a4256"),
            "main": EDGE2.get(rep["confidence"], "#3a4256"),
        },
    }
    # Embedded in <script>var J=…</script>. The travelers carry raw commit
    # subjects / branch / component names, so neutralize every character that
    # could break out of the script element (a commit subject like
    # "<!--<script>" would otherwise desync the HTML parser).
    payload = (json.dumps(journey)  # ensure_ascii=True escapes U+2028/2029
               .replace("<", "\\u003c").replace(">", "\\u003e")
               .replace("&", "\\u0026"))

    # Production surfaces as a caption row under the LIVE zone
    SURFACE_SHORT = {
        "match": ("#22c55e", "running the latest ✓"),
        "behind": ("#eab308", "{lag} behind"),
        "ahead": ("#eab308", "ahead of local — run --fetch"),
        "diverged": ("#ef4444", "NOT on the deploy branch ✗"),
        "unresolved": ("#eab308", "unknown build — run --fetch"),
        "up": ("#94a3b8", "reachable, build unproven"),
        "down": ("#ef4444", "DOWN ✗"),
    }
    surf_chips = []
    for s in rep["surfaces"]:
        col, txt = SURFACE_SHORT.get(s["status"], ("#94a3b8", s["status"]))
        txt = txt.format(lag=plural(s["lag"], "commit") if s.get("lag") else "slightly")
        name = s["label"]
        if s["source"] == "github-deployment":
            name = "deployment record"
        elif s["source"] == "deploy-workflow":
            name = "deploy run"
        elif s["source"] == "url":
            name = re.sub(r"^https?://", "", s["label"]).rstrip("/")
        surf_chips.append(f'<span>{dot(col)} <b>{esc(name)}</b> '
                          f'<span style="color:{col}">{esc(txt)}</span></span>')
    if not surf_chips:
        hint = ("offline run — production not checked" if rep["offline"]
                else "no production surfaces known — run --init"
                + ("" if rep["gh_available"] or not rep["slug"] else ", or install gh"))
        surf_chips.append(f'<span>{dot("#94a3b8")} {esc(hint)}</span>')

    journey_html = (
        '<div class=jwrap><h2>The Journey — your work traveling to live</h2>'
        '<svg id=jsvg viewBox="0 0 1000 380" role="img" '
        'aria-label="Map of work traveling from your laptop to live"></svg>'
        f'<div class=jsurf>LIVE runs on: {" ".join(surf_chips)}</div>'
        '<div id=jpanel></div>'
        '<div class=jlegend>Each bubble is a piece of your project · the further right, '
        'the closer to real users · click any bubble for its story in plain English</div>'
        f'<script>var J={payload};{JOURNEY_JS}</script></div>')

    nr = newest_rung(rep)
    maxc = max([1] + list(rep["counts"].values()))
    ladder_rows = []
    for r in range(8, 0, -1):
        label, meaning = RUNGS[r]
        n = rep["counts"][r]
        unit = "files" if r <= 2 else "commits"
        pct = int((n / maxc) * 100) if n else 0
        col = RUNG_COLORS[r]
        mark = ' <span class="mark">← newest work is HERE</span>' if r == nr else ""
        ladder_rows.append(
            f'<div class="rung"><div class="rl"><b>{r}</b> {esc(label)} '
            f'<span class="meaning">{esc(meaning)}</span></div>'
            f'<div class="bar"><div class="fill" style="width:{pct}%;background:{col}"></div>'
            f'<span class="cnt">{n} {unit}</span>{mark}</div></div>'
        )

    def row(cm, muted=False):
        col = RUNG_COLORS[cm["rung"]]
        cls = ' class="muted"' if muted else ""
        btn = ""
        if not muted and cm["rung"] < 8 and cm["action"] != "no action":
            prompt = fix_prompt(rep, cm["subject"], cm["sha"], cm["rung"], cm["plain"], cm["action"])
            btn = f' <button class=copy data-prompt="{esc(prompt)}">copy fix prompt</button>'
        return (f"<tr{cls}><td class=sha>{esc(cm['sha'])}</td><td>{esc(cm['subject'])}</td>"
                f"<td><span class=pill style='background:{col}'>{cm['rung']} {esc(RUNGS[cm['rung']][0])}</span></td>"
                f"<td>{esc(cm['plain'])}</td><td class=act>▶ {esc(cm['action'])}{btn}</td></tr>")

    climbing = [cm for cm in rep["classified"] if cm["rung"] < 8]
    done = [cm for cm in rep["classified"] if cm["rung"] >= 8]
    notlive_html = ""
    if climbing:
        notlive_rows = "\n".join(row(cm) for cm in climbing)
        notlive_html = (f'<div class=notlive><h2>Not live yet — '
                        f'{plural(len(climbing), "commit")}, newest first</h2>'
                        f'<table><tbody>{notlive_rows}</tbody></table></div>')
    all_rows = "\n".join([row(cm) for cm in climbing]
                         + [row(cm, muted=True) for cm in done]) or \
        "<tr><td colspan=5>no commits found</td></tr>"
    commits_summary = (f"Recent commits — {plural(len(rep['classified']), 'commit')} checked"
                       + (f" · {len(done)} live ✓" if done else "")
                       + (f" · {len(climbing)} not live yet" if climbing else ""))

    live = rep["live"]
    live_lines = []
    if rep["offline"]:
        live_lines.append("offline run — rungs 7-8 were not checked")
        live_lines.extend(esc(u) for u in live.get("configured_urls", []))
    else:
        for (url, status, ok) in live.get("urls", []):
            live_lines.append(f"{'✓' if ok else '✗'} {esc(url)} (HTTP {status})")
        if live.get("bundle"):
            live_lines.append(f"served build asset: {esc(live['bundle'])}")
        for vr in live.get("version_results", []):
            if vr.get("sha_raw") and not vr.get("error"):
                live_lines.append(f"{esc(vr['label'])}: deployed commit {esc(vr['sha_raw'][:12])}")
            elif vr.get("sha_raw"):
                live_lines.append(f"{esc(vr['label'])}: reports {esc(vr['sha_raw'][:12])} ⚠ {esc(vr['error'])}")
            else:
                live_lines.append(f"{esc(vr['label'])}: version check failed — {esc(vr['error'])}")
        prod = (rep["deployments"] or {}).get("production")
        if prod:
            live_lines.append(f"GitHub deployment: {esc(prod['sha'][:12])} → {esc(prod.get('environment'))}")
        for r_info in rep["deploy_runs"]:
            live_lines.append(f"deploy workflow '{esc(r_info['workflow'])}': success @ {esc(r_info['sha'][:12])}")
        if rep["deploy_lag"]:
            live_lines.append(f"production is {rep['deploy_lag']} commits behind {esc(rep['default'])}")
        elif rep["confidence"] == "verified":
            live_lines.append(f"production matches the tip of {esc(rep['default'])} ✓")
        if not rep["proofs"] and live.get("checked"):
            live_lines.append("exact deployed commit: UNVERIFIED")
        if not live_lines:
            live_lines.append("no production URL configured (local ladder still accurate)")
    if live.get("notes"):
        live_lines.append("note: " + esc(live["notes"]))
    live_html = "<br>".join(live_lines)

    bs = rep["branch_split"]
    parked_lines = []
    if bs:
        if bs["squash_merged"]:
            parked_lines.append(
                f"{len(bs['truly_unmerged'])} branches truly unmerged · "
                f"{len(bs['squash_merged'])} look unmerged but were squash-merged "
                "(safe to clean up)")
        else:
            parked_lines.append(f"{bs['not_in_main']} of {bs['total_local']} local branches "
                                f"are not in {esc(rep['default'])}")
        if bs["truly_unmerged"]:
            parked_lines.append(f"{len(bs['active'])} touched in the last "
                                f"{STALE_BRANCH_DAYS} days · {len(bs['stale'])} stale")
            if bs["notable"]:
                parked_lines.append("most recent: " + esc("  ·  ".join(bs["notable"])))
    if rep["ahead_main"]:
        parked_lines.append(f"this branch is {plural(rep['ahead_main'], 'commit')} "
                            f"ahead of {esc(rep['default'])}")
    trees = rep["worktrees"]
    if len(trees) > 1:
        scratch = len([t for t in trees if t.get("agent_scratch")])
        manual_det = len([t for t in trees if t.get("detached") and not t.get("agent_scratch")])
        tline = f"{len(trees)} worktrees" + (f" · {scratch} agent-scratch" if scratch else "")
        if manual_det:
            tline += f" · ⚠ {manual_det} hand-made detached (at risk)"
        parked_lines.append(tline)
    parked_html = "<br>".join(parked_lines) or "nothing parked"
    parked_summary = ("Branches &amp; worktrees — "
                      + (f"{len(bs['truly_unmerged'])} truly unmerged" if bs else "details"))

    offline_note = ('<div class=offnote>OFFLINE SNAPSHOT — production was not checked this run</div>'
                    if rep["offline"] else "")

    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>is-it-live · {esc(os.path.basename(rep['root']))}</title>
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;background:#0b0e14 radial-gradient(1100px 520px at 72% -8%,#111a2e 0%,rgba(11,14,20,0)60%) no-repeat;color:#c9d1e0;margin:0;padding:30px 22px}}
.wrap{{max-width:1020px;margin:0 auto}}
.hero{{display:flex;gap:18px;align-items:flex-start;margin:6px 0 22px}}
.bigdot{{width:20px;height:20px;border-radius:50%;margin-top:11px;flex:none}}
.htext{{font-size:31px;font-weight:650;letter-spacing:-.02em;line-height:1.2}}
.hsub{{color:#9aa4b8;font-size:15px;margin-top:7px}}
.problem{{color:#f87171;margin-top:7px;font-size:15px;font-weight:600}}
.hmeta{{color:#5d6678;font-size:12.5px;margin-top:9px}}
.pulse{{color:#22c55e}}
.offnote{{background:#3b2f12;color:#fbbf24;border-radius:10px;padding:10px 16px;margin:0 0 18px;font-size:13.5px}}
.action{{background:#0c2d36;border:1px solid #155e75;color:#a5f3fc;padding:13px 16px;border-radius:11px;margin:0 0 8px;font-size:14.5px}}
h2{{font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:#5d6678;margin:0 0 10px;font-weight:600}}
.mapwrap{{margin:28px 0}}
.dot{{display:inline-block;border-radius:50%;flex:none;vertical-align:baseline}}
.imp{{background:#312e81;color:#c7d2fe;border-radius:5px;padding:1px 8px;font-size:10px;text-transform:uppercase;letter-spacing:.06em;margin-left:6px}}
.copy{{background:#1c2230;color:#8fa6d4;border:1px solid #2d3648;border-radius:6px;padding:2px 9px;font:inherit;font-size:11px;cursor:pointer;margin-left:8px}}
.copy:hover{{background:#2d3648}}
.jwrap{{margin:28px 0}}
#jsvg{{width:100%;height:auto;display:block}}
.zone{{animation:zonepulse 4.5s ease-in-out infinite}}
@keyframes zonepulse{{0%,100%{{stroke-opacity:.4}}50%{{stroke-opacity:1}}}}
.stlabel{{fill:#8b94a7;font-size:12px;letter-spacing:.12em;font-weight:600}}
.bubble{{cursor:pointer}}
.bubble circle{{transition:stroke-width .15s}}
.bubble:hover circle{{stroke-width:3}}
.bubble.sel circle{{stroke-width:3}}
.blabel{{fill:#c3cad9;font-size:11.5px;font-weight:500}}
.bubble.sel .blabel{{fill:#f1f4fa;font-weight:650}}
.check{{fill:#22c55e;font-size:14px;font-weight:700}}
.jsurf{{display:flex;gap:20px;justify-content:flex-end;font-size:12.5px;color:#5d6678;margin:2px 4px 10px;flex-wrap:wrap;align-items:center}}
.jsurf b{{color:#9aa4b8;font-weight:600}}
#jpanel{{background:#11151f;border:1px solid #1c2230;border-radius:12px;padding:14px 18px;min-height:80px}}
.pname{{font-weight:650;font-size:15.5px;color:#dde3ee;margin-bottom:5px}}
.pline{{color:#9aa4b8;font-size:13.5px;line-height:1.7}}
.pact{{color:#86efac;margin-top:9px;font-size:13.5px}}
.jlegend{{color:#4d5566;font-size:12px;margin-top:9px}}
.notlive{{border:1px solid #6d5410;border-radius:12px;background:#171204;padding:14px 16px 8px;margin:28px 0}}
.notlive h2{{color:#fbbf24}}
details.acc{{background:#11151f;border:1px solid #1c2230;border-radius:12px;margin:10px 0}}
details.acc>summary{{list-style:none;cursor:pointer;padding:13px 16px;font-size:14px;font-weight:600;color:#c9d1e0}}
details.acc>summary::-webkit-details-marker{{display:none}}
details.acc>summary::before,details.comp summary .csum::before{{content:'▸';color:#5d6678;margin-right:10px;display:inline-block;transition:transform .15s}}
details[open].acc>summary::before,details.comp[open] summary .csum::before{{transform:rotate(90deg)}}
.accbody{{padding:2px 16px 16px;font-size:13px;color:#9aa4b8;line-height:1.9}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
thead td{{color:#5d6678;font-size:11px;text-transform:uppercase;letter-spacing:.08em}}
td{{padding:8px;border-top:1px solid #1a1f2b;vertical-align:top}}
.sha{{font-family:ui-monospace,Menlo,monospace;color:#8fa6d4;font-weight:600;white-space:nowrap}}
.pill{{color:#0b0e14;font-weight:700;padding:2px 8px;border-radius:20px;font-size:11px;white-space:nowrap}}
.act{{color:#86efac}}
.muted td{{color:#525b6e}}
.rung{{margin:7px 0}}
.rl{{font-size:12px;margin-bottom:2px}}
.rl b{{color:#c9d1e0}}.meaning{{color:#5d6678}}
.bar{{position:relative;background:#0b0e14;border-radius:5px;height:20px;display:flex;align-items:center}}
.fill{{position:absolute;left:0;top:0;bottom:0;border-radius:5px;opacity:.28}}
.cnt{{position:relative;color:#7f849c;margin-left:8px;font-size:12px}}
.mark{{position:relative;color:#f38ba8;font-size:11px;margin-left:8px}}
.foot{{color:#4d5566;font-size:11.5px;margin-top:28px}}
</style></head><body><div class=wrap>
{hero_html}
{offline_note}
{action_html}
{journey_html}
{notlive_html}
<div class=mapwrap><h2>Dig deeper</h2>
<details class=acc><summary>{commits_summary}</summary>
<div class=accbody><table><thead><tr><td>commit</td><td>what</td><td>rung</td><td>plain english</td><td>next action</td></tr></thead>
<tbody>{all_rows}</tbody></table></div></details>
<details class=acc><summary>{parked_summary}</summary><div class=accbody>{parked_html}</div></details>
<details class=acc><summary>Evidence — every check this run</summary><div class=accbody>{live_html}
<h2 style="margin-top:20px">The Ladder</h2>{''.join(ladder_rows)}</div></details>
</div>
<div class=foot>read-only {'live dashboard' if live_mode else 'snapshot'} · is-it-live v{__version__} · generated {esc(rep['generated'])}{'' if live_mode else ' · re-run to refresh'}</div>
<script>
document.addEventListener('click', function(e) {{
  if (!e.target.classList.contains('copy')) return;
  navigator.clipboard.writeText(e.target.dataset.prompt).then(function() {{
    e.target.textContent = 'copied ✓';
    setTimeout(function() {{ e.target.textContent = 'copy fix prompt'; }}, 1500);
  }});
}});
{f'''var DIGEST = "{esc(rep['state_digest'])}";
setInterval(function() {{
  fetch('/data.json').then(function(r) {{ return r.json(); }}).then(function(d) {{
    if (d.state_digest !== DIGEST) location.reload();
  }}).catch(function() {{}});
}}, {max(5, interval // 2) * 1000});''' if live_mode else ''}
</script>
</body></html>"""


# --------------------------------------------------------------------------- #
# Live dashboard (--serve): the living, breathing version of the HTML report
# --------------------------------------------------------------------------- #
def serve_dashboard(root, args):
    """Local-only web dashboard that re-checks the repo on an interval.

    Still read-only: each refresh is the same work as a normal run (plus a
    `git fetch origin <default>` per cycle when --fetch was given).
    """
    import http.server
    import threading

    interval = max(15, args.interval)
    state = {"ts": 0.0, "html": b"", "json": b""}
    swap = threading.Lock()       # guards swapping/reading the rendered bodies
    regen_gate = threading.Lock()  # ensures only one regen runs at a time

    def regen():
        if not repo_root(root):
            return  # repo vanished mid-serve: keep the last good snapshot
        cfg = load_config(root)
        use_net = not args.no_net
        if args.fetch and use_net:
            default = detect_default_branch(root, cfg.get("production_branch"))
            git(["fetch", "--quiet", "origin", "--", default], root, timeout=45,
                env_extra={"GIT_TERMINAL_PROMPT": "0"})
        rep = build_report(root, cfg, args.limit, use_net, args.fetch)
        html = render_html(rep, live_mode=True, interval=interval).encode("utf-8")
        body = json.dumps(rep, default=str).encode("utf-8")
        with swap:
            state["html"], state["json"] = html, body
            state["ts"] = datetime.now().timestamp()

    def safe_regen():
        """regen() that never propagates — a transient build failure must not
        wedge the cold-start path into re-failing on every request."""
        try:
            regen()
        except Exception as e:  # noqa: BLE001
            if state["ts"] == 0.0:
                import html as _h
                msg = (f"<!doctype html><meta charset=utf-8>"
                       f"<body style='font-family:sans-serif'>is-it-live: could not build "
                       f"the dashboard ({_h.escape(str(e))}). Retrying on the next request."
                       ).encode("utf-8")
                with swap:
                    state["html"] = msg
                    state["json"] = json.dumps({"error": str(e)}).encode("utf-8")
                    state["ts"] = datetime.now().timestamp()

    def ensure_fresh():
        """First request blocks until content exists; afterwards a stale cache
        triggers ONE background regen while everyone is served the snapshot."""
        if state["ts"] == 0.0:
            with regen_gate:
                if state["ts"] == 0.0:
                    safe_regen()
            return
        if datetime.now().timestamp() - state["ts"] <= interval:
            return
        if regen_gate.acquire(blocking=False):
            def work():
                try:
                    safe_regen()
                finally:
                    regen_gate.release()
            threading.Thread(target=work, daemon=True).start()

    def host_is_local(headers):
        host = (headers.get("Host") or "").strip()
        if host.startswith("["):
            host = host[1:host.find("]")]
        else:
            host = host.split(":")[0]
        return host in ("127.0.0.1", "localhost", "::1", "")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not host_is_local(self.headers):
                self.send_response(403)
                self.end_headers()
                return
            ensure_fresh()
            with swap:
                if self.path.startswith("/data.json"):
                    body, ctype = state["json"], "application/json"
                else:
                    body, ctype = state["html"], "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: D102
            pass

    try:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.serve), Handler)
    except OSError as e:
        print(f"is-it-live: cannot bind 127.0.0.1:{args.serve} ({e})", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"is-it-live dashboard: {url}")
    print(f"  repo: {root}")
    print(f"  refreshes every {interval}s"
          + (" (with git fetch)" if args.fetch else "") + " · Ctrl-C to stop")
    if args.open:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nis-it-live: stopped.")
    return 0


# --------------------------------------------------------------------------- #
# Reports dir, --init scaffolding
# --------------------------------------------------------------------------- #
def reports_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "is-it-live")


def prune_old_reports(directory):
    try:
        cutoff = datetime.now().timestamp() - REPORT_MAX_AGE_DAYS * 86400
        for name in os.listdir(directory):
            p = os.path.join(directory, name)
            if name.endswith(".html") and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except OSError:
        pass


def write_html_report(rep, html_arg, root):
    if html_arg == "__auto__":
        directory = reports_dir()
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            directory = tempfile.gettempdir()
        prune_old_reports(directory)
        slug = os.path.basename(root)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(directory, f"{slug}-{stamp}.html")
    else:
        path = os.path.abspath(html_arg)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_html(rep))
    except OSError:
        path = os.path.join(tempfile.gettempdir(), f"is-it-live-{os.path.basename(root)}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_html(rep))
    return path


INIT_TEMPLATE = {
    "_comment": "is-it-live config for this repo. All fields optional; delete what you don't use. Docs: SKILL.md in the is-it-live skill folder.",
    "provider": "auto",
    "production_branch": "main",
    "production_urls": [],
    "version_sources": [
        {
            "label": "app",
            "url": "https://YOUR-PROD-DOMAIN/api/version",
            "json_path": "commit",
        }
    ],
    "deploy_workflows": [],
    "ignore_branch_prefixes": [],
}


def scaffold_init(root):
    """Write a starter .is-it-live.json (the ONE write --init is allowed to do)."""
    path = os.path.join(root, ".is-it-live.json")
    if os.path.exists(path):
        print(f"is-it-live: {path} already exists — not overwriting.")
        return 1
    cfg = dict(INIT_TEMPLATE)
    detected = detect_provider(root, {})
    if detected != "unknown":
        cfg["provider"] = detected
    default = detect_default_branch(root, None)
    cfg["production_branch"] = default
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print(f"is-it-live: wrote starter config to {path}")
    print("Edit production_urls and version_sources, then re-run is-it-live.")
    print("Tip: a version endpoint that echoes the deployed commit SHA is the single")
    print("best upgrade — it makes 'is it live' PROVABLE from anywhere.")
    return 0


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    ap = argparse.ArgumentParser(
        description="Read-only map of where your code stands, dirty tree -> live.")
    ap.add_argument("--path", default=os.getcwd(), help="repo path (default: cwd)")
    ap.add_argument("--limit", type=int, default=20, help="recent commits to classify")
    ap.add_argument("--fetch", action="store_true",
                    help="git fetch origin <default-branch> first (refresh remote info)")
    ap.add_argument("--no-net", action="store_true", help="skip all network (gh + live checks)")
    ap.add_argument("--html", nargs="?", const="__auto__", help="write HTML report (optional path)")
    ap.add_argument("--open", action="store_true", help="open the HTML report in the browser")
    ap.add_argument("--color", action="store_true", help="ANSI colors (for a real terminal)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--init", action="store_true",
                    help="write a starter .is-it-live.json into the repo root")
    ap.add_argument("--serve", nargs="?", const=4756, type=int, metavar="PORT",
                    help="run a local live dashboard (default port 4756; refreshes on an interval)")
    ap.add_argument("--interval", type=int, default=60,
                    help="refresh interval in seconds for --serve (min 15, default 60)")
    ap.add_argument("--version", action="version", version=f"is-it-live {__version__}")
    args = ap.parse_args()

    root = repo_root(args.path)
    if not root:
        print(f"is-it-live: not a git repo: {args.path}", file=sys.stderr)
        sys.exit(2)

    if args.init:
        sys.exit(scaffold_init(root))

    if args.serve is not None:
        sys.exit(serve_dashboard(root, args))

    cfg = load_config(root)
    use_net = not args.no_net
    fetched = False
    if args.fetch and use_net:
        default = detect_default_branch(root, cfg.get("production_branch"))
        code, _, _ = git(["fetch", "--quiet", "origin", "--", default], root, timeout=45,
                         env_extra={"GIT_TERMINAL_PROMPT": "0"})
        fetched = code == 0

    rep = build_report(root, cfg, args.limit, use_net, fetched)

    html_path = None
    if args.html is not None:
        html_path = write_html_report(rep, args.html, root)
        rep["html_report"] = html_path

    if args.json:
        print(json.dumps(rep, indent=2, default=str))
        if html_path:
            print(f"HTML dashboard: {html_path}", file=sys.stderr)
    else:
        print(render_terminal(rep, color=args.color))
        if html_path:
            print(f"\n  HTML dashboard: {html_path}")

    if html_path and args.open:
        import webbrowser
        try:
            from pathlib import Path
            webbrowser.open(Path(html_path).as_uri())
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
