#!/usr/bin/env python3
"""Smoke + regression tests for is_it_live.py. Stdlib only — run directly:

    python3 test_is_it_live.py

Every test builds throwaway repos under a temp dir; nothing touches real repos
or the network (the version-endpoint tests use a localhost HTTP server).
"""

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "is_it_live.py")
GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.com"]


def sh(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)


def git(args, cwd):
    return sh(["git"] + GIT_ID + args, cwd=cwd)


def run_tool(args, cwd, env_extra=None):
    env = dict(os.environ)
    # Keep gh fully out of tests regardless of host environment: blank the token
    # and point gh at an unresolvable host so it can never make a real call.
    env["GH_TOKEN"] = ""
    env["GH_HOST"] = "invalid.localhost"
    env.pop("GH_REPO", None)
    if env_extra:
        env.update(env_extra)
    return sh([sys.executable, TOOL] + args, cwd=cwd, env=env)


def make_repo(base, name="repo", default="main", commits=2):
    root = os.path.join(base, name)
    os.makedirs(root)
    r = git(["init", "-b", default], cwd=root)
    if r.returncode != 0:  # very old git: no -b
        git(["init"], cwd=root)
        git(["checkout", "-b", default], cwd=root)
    for i in range(commits):
        with open(os.path.join(root, f"f{i}.txt"), "w") as f:
            f.write(f"content {i}\n")
        git(["add", "."], cwd=root)
        git(["commit", "-m", f"commit {i}"], cwd=root)
    return root


class VersionServer:
    """Localhost server returning a fixed body on every path."""

    def __init__(self, body, status=200):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                payload = outer.body.encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.body = body
        self.status = status
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/version"

    def stop(self):
        self.httpd.shutdown()


class IsItLiveTests(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="iil-test-")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # ---- basic shapes -------------------------------------------------- #

    def test_offline_run_is_honest_about_unknown_live_state(self):
        root = make_repo(self.base)
        r = run_tool(["--no-net"], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MERGED", r.stdout)
        self.assertIn("NOT CHECKED", r.stdout)
        # The old bug: a definitive negative claim with zero live information.
        self.assertNotIn("not yet LIVE", r.stdout)
        self.assertNotIn("NOTHING recent is LIVE", r.stdout)

    def test_json_contract(self):
        root = make_repo(self.base)
        r = run_tool(["--no-net", "--json"], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["schema_version"], 2)
        v = data["verdict"]
        self.assertEqual(v["verdict"], "unknown")  # merged but live not checked
        self.assertEqual(v["confidence"], "not_checked")
        self.assertIn("headline", v)
        self.assertIn("next_action", v)
        self.assertIn("generated_iso", data)
        self.assertTrue(data["classified"])
        self.assertIn("full_sha", data["classified"][0])

    def test_empty_repo_no_commits(self):
        root = os.path.join(self.base, "empty")
        os.makedirs(root)
        git(["init", "-b", "main"], cwd=root)
        r = run_tool(["--no-net"], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_detached_head(self):
        root = make_repo(self.base)
        git(["checkout", "--detach"], cwd=root)
        r = run_tool(["--no-net", "--json"], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertTrue(data["detached"])

    def test_master_default_branch(self):
        root = make_repo(self.base, default="master")
        r = run_tool(["--no-net", "--json"], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["default"], "master")

    def test_ascii_stdout_does_not_crash(self):
        root = make_repo(self.base)
        r = run_tool(["--no-net"], cwd=root, env_extra={"PYTHONIOENCODING": "ascii"})
        self.assertEqual(r.returncode, 0, r.stderr)

    # ---- the stdout-strip / porcelain regression ----------------------- #

    def test_unstaged_dotfile_first_in_status_is_not_misbucketed(self):
        root = make_repo(self.base, commits=1)
        # Tracked dotfile path that sorts FIRST in porcelain output, then modify
        # it without staging. v1 stripped stdout globally, shifting the XY
        # columns of the first line: the file landed in "staged" and lost the
        # leading "." of its path.
        dotdir = os.path.join(root, ".github")
        os.makedirs(dotdir)
        fp = os.path.join(dotdir, "workflow.yml")
        with open(fp, "w") as f:
            f.write("a\n")
        git(["add", "."], cwd=root)
        git(["commit", "-m", "add dotfile"], cwd=root)
        with open(fp, "a") as f:
            f.write("b\n")
        r = run_tool(["--no-net", "--json"], cwd=root)
        data = json.loads(r.stdout)
        self.assertEqual(data["staged"], [])
        self.assertEqual(data["unstaged"], [".github/workflow.yml"])

    def test_deleted_files_reported_as_deleted_not_modified(self):
        root = make_repo(self.base, commits=2)
        os.remove(os.path.join(root, "f0.txt"))
        r = run_tool(["--no-net", "--json"], cwd=root)
        data = json.loads(r.stdout)
        self.assertEqual(data["wt_detail"]["deleted"], 1)
        self.assertEqual(data["wt_detail"]["modified"], 0)

    # ---- worktrees ------------------------------------------------------ #

    def test_worktree_slashed_branch_name_preserved(self):
        root = make_repo(self.base)
        git(["branch", "feature/foo"], cwd=root)
        wt = os.path.join(self.base, "wt")
        git(["worktree", "add", wt, "feature/foo"], cwd=root)
        r = run_tool(["--no-net", "--json"], cwd=root)
        data = json.loads(r.stdout)
        branches = {t["branch"] for t in data["worktrees"]}
        self.assertIn("feature/foo", branches)

    # ---- version endpoint: the rungs 7/8 evidence path ------------------ #

    def _repo_with_version_source(self, body, json_path=None, commits=3):
        root = make_repo(self.base, commits=commits)
        server = VersionServer(body)
        self.addCleanup(server.stop)
        cfg = {"version_sources": [{"label": "app", "url": server.url}]}
        if json_path:
            cfg["version_sources"][0]["json_path"] = json_path
        with open(os.path.join(root, ".is-it-live.json"), "w") as f:
            json.dump(cfg, f)
        git(["add", ".is-it-live.json"], cwd=root)
        git(["commit", "-m", "add is-it-live config"], cwd=root)
        return root, server

    def _shas(self, root):
        r = git(["log", "--pretty=%H", "main"], cwd=root)
        return r.stdout.split()  # newest first

    def test_deployed_boundary_old_commit_live_new_commit_waiting(self):
        root, server = self._repo_with_version_source("PLACEHOLDER")
        shas = self._shas(root)          # [newest, middle, oldest]
        server.body = shas[1]            # deployed = middle commit
        r = run_tool(["--json"], cwd=root)
        data = json.loads(r.stdout)
        by_sha = {c["full_sha"]: c for c in data["classified"]}
        self.assertEqual(by_sha[shas[1]]["rung"], 8)   # deployed commit: live
        self.assertEqual(by_sha[shas[2]]["rung"], 8)   # ancestor of deploy: live
        self.assertEqual(by_sha[shas[0]]["rung"], 6)   # after deploy: waiting
        self.assertIn("deploy", by_sha[shas[0]]["action"].lower())
        self.assertEqual(data["deploy_lag"], 1)
        self.assertEqual(data["verdict"]["confidence"], "verified")

    def test_everything_live_when_deploy_matches_tip(self):
        root, server = self._repo_with_version_source("PLACEHOLDER")
        shas = self._shas(root)
        server.body = shas[0]
        r = run_tool(["--json"], cwd=root)
        data = json.loads(r.stdout)
        self.assertTrue(all(c["rung"] == 8 for c in data["classified"]))
        self.assertEqual(data["verdict"]["verdict"], "live")
        self.assertTrue(data["verdict"]["next_action"]["done"])

    def test_short_garbage_version_body_never_claims_live(self):
        # v1 bug: a 1-char body that happened to be a hex prefix of the tip
        # flipped the entire history to LIVE.
        root, server = self._repo_with_version_source("PLACEHOLDER")
        shas = self._shas(root)
        server.body = shas[0][0]  # single hex char, prefix of the real tip
        r = run_tool(["--json"], cwd=root)
        data = json.loads(r.stdout)
        self.assertTrue(all(c["rung"] < 8 for c in data["classified"]))
        errors = [v["error"] for v in data["live"]["version_results"]]
        self.assertTrue(any("not a commit SHA" in (e or "") for e in errors))

    def test_html_version_body_with_json_path_never_claims_mismatch(self):
        # v1 bug: SPA-fallback HTML leaked as "deployed commit <!doctype ht"
        # with a definitive "does NOT match" verdict.
        root, _ = self._repo_with_version_source(
            "<!doctype html><html><body>app</body></html>", json_path="commit")
        r = run_tool(["--json"], cwd=root)
        data = json.loads(r.stdout)
        vr = data["live"]["version_results"][0]
        self.assertIsNone(vr["sha_raw"])
        self.assertIn("non-JSON", vr["error"])
        self.assertNotIn("does NOT match", r.stdout)

    def test_json_version_source_happy_path(self):
        root, server = self._repo_with_version_source("PLACEHOLDER", json_path="commit")
        shas = self._shas(root)
        server.body = json.dumps({"commit": shas[0]})
        r = run_tool(["--json"], cwd=root)
        data = json.loads(r.stdout)
        self.assertEqual(data["live"]["version_results"][0]["sha_full"], shas[0])
        self.assertEqual(data["verdict"]["verdict"], "live")

    def test_unresolved_proof_sha_never_yields_verified_live(self):
        # A surface reporting a deployed commit that is NOT in local history
        # must cap confidence (proof_unresolved) and never produce "LIVE ✓".
        root, server = self._repo_with_version_source("PLACEHOLDER")
        server.body = "a" * 40  # valid hex, unknown commit
        r = run_tool(["--json"], cwd=root)
        data = json.loads(r.stdout)
        self.assertEqual(data["verdict"]["confidence"], "proof_unresolved")
        self.assertNotEqual(data["verdict"]["verdict"], "live")
        self.assertTrue(all(c["rung"] < 8 for c in data["classified"]))
        self.assertIn("--fetch", data["classified"][0]["action"])

    def test_mixed_resolved_and_unresolved_proofs_cap_at_rung_7(self):
        root = make_repo(self.base, commits=3)
        good = VersionServer("PLACEHOLDER")
        bad = VersionServer("b" * 40)
        self.addCleanup(good.stop)
        self.addCleanup(bad.stop)
        cfg = {"version_sources": [
            {"label": "frontend", "url": good.url},
            {"label": "backend", "url": bad.url},
        ]}
        with open(os.path.join(root, ".is-it-live.json"), "w") as f:
            json.dump(cfg, f)
        git(["add", "."], cwd=root)
        git(["commit", "-m", "config"], cwd=root)
        good.body = self._shas(root)[0]  # frontend serves the real tip
        r = run_tool(["--json"], cwd=root)
        data = json.loads(r.stdout)
        self.assertTrue(all(c["rung"] <= 7 for c in data["classified"]))
        self.assertEqual(data["classified"][0]["rung"], 7)
        self.assertEqual(data["verdict"]["confidence"], "proof_unresolved")

    def test_unshipped_work_gets_definitive_gap_banner_even_unconfigured(self):
        # Banner/verdict consistency: rung <=4 work is definitively not live;
        # the "NOT CHECKED ... cannot say" wording is reserved for merged work.
        root = make_repo(self.base)
        git(["checkout", "-b", "feature/x"], cwd=root)
        with open(os.path.join(root, "new.txt"), "w") as f:
            f.write("x\n")
        git(["add", "."], cwd=root)
        git(["commit", "-m", "feature work"], cwd=root)
        r = run_tool(["--no-net", "--json"], cwd=root)
        data = json.loads(r.stdout)
        self.assertEqual(data["verdict"]["verdict"], "not_shipped")
        self.assertNotIn("cannot say", data["verdict"]["headline"])
        self.assertIn("GAP", data["verdict"]["headline"])

    def test_empty_repo_action_does_not_claim_verified_live(self):
        root = os.path.join(self.base, "empty")
        os.makedirs(root)
        git(["init", "-b", "main"], cwd=root)
        r = run_tool(["--no-net", "--json"], cwd=root)
        data = json.loads(r.stdout)
        self.assertNotIn("verified live", data["verdict"]["next_action"]["text"])

    def test_json_plus_html_writes_both(self):
        root = make_repo(self.base)
        out = os.path.join(self.base, "combo.html")
        r = run_tool(["--no-net", "--json", "--html", out], cwd=root)
        data = json.loads(r.stdout)
        self.assertEqual(data["html_report"], out)
        self.assertTrue(os.path.exists(out))

    # ---- components + surfaces (the visual-map layer) -------------------- #

    def test_components_classified_by_their_own_paths(self):
        root = make_repo(self.base, commits=1)
        os.makedirs(os.path.join(root, "frontend"))
        os.makedirs(os.path.join(root, "backend"))
        with open(os.path.join(root, "frontend", "app.js"), "w") as f:
            f.write("a\n")
        git(["add", "."], cwd=root)
        git(["commit", "-m", "frontend work"], cwd=root)
        with open(os.path.join(root, "backend", "api.py"), "w") as f:
            f.write("b\n")
        git(["add", "."], cwd=root)
        git(["commit", "-m", "backend work"], cwd=root)
        cfg = {"components": [
            {"name": "Frontend", "paths": ["frontend/"], "importance": "core"},
            {"name": "Backend", "paths": ["backend/"]},
            {"name": "Ghost", "paths": ["nonexistent/"]},
        ]}
        with open(os.path.join(root, ".is-it-live.json"), "w") as f:
            json.dump(cfg, f)
        # dirty file inside frontend should be counted against that component
        with open(os.path.join(root, "frontend", "app.js"), "a") as f:
            f.write("dirty\n")
        r = run_tool(["--no-net", "--json"], cwd=root)
        data = json.loads(r.stdout)
        comps = {c["name"]: c for c in data["components"]}
        self.assertEqual(comps["Frontend"]["last"]["subject"], "frontend work")
        self.assertEqual(comps["Backend"]["last"]["subject"], "backend work")
        self.assertIsNone(comps["Ghost"]["last"])
        self.assertEqual(comps["Frontend"]["dirty_files"], 1)
        self.assertEqual(comps["Backend"]["dirty_files"], 0)
        self.assertEqual(comps["Frontend"]["rung"], 6)  # merged, live not checked
        self.assertEqual(comps["Frontend"]["importance"], "core")
        # terminal renders the section
        r2 = run_tool(["--no-net"], cwd=root)
        self.assertIn("COMPONENTS", r2.stdout)
        self.assertIn("Frontend", r2.stdout)

    def test_surfaces_report_match_status(self):
        root, server = self._repo_with_version_source("PLACEHOLDER")
        server.body = self._shas(root)[0]
        r = run_tool(["--json"], cwd=root)
        data = json.loads(r.stdout)
        version_surfaces = [s for s in data["surfaces"] if s["source"] == "version-endpoint"]
        self.assertEqual(len(version_surfaces), 1)
        self.assertEqual(version_surfaces[0]["status"], "match")

    def test_surfaces_report_lag_when_deploy_behind(self):
        root, server = self._repo_with_version_source("PLACEHOLDER")
        server.body = self._shas(root)[1]
        r = run_tool(["--json"], cwd=root)
        data = json.loads(r.stdout)
        s = [s for s in data["surfaces"] if s["source"] == "version-endpoint"][0]
        self.assertEqual(s["status"], "behind")
        self.assertEqual(s["lag"], 1)

    def test_html_includes_journey_and_copy_prompt(self):
        root = make_repo(self.base)
        git(["checkout", "-b", "feat/x"], cwd=root)
        with open(os.path.join(root, "z.txt"), "w") as f:
            f.write("z\n")
        git(["add", "."], cwd=root)
        git(["commit", "-m", "unshipped"], cwd=root)
        out = os.path.join(self.base, "map.html")
        r = run_tool(["--no-net", "--html", out], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out) as f:
            html = f.read()
        self.assertIn("The Journey", html)
        self.assertIn("YOUR LAPTOP", html)
        self.assertIn("jsvg", html)
        self.assertIn("copy fix prompt", html)
        self.assertIn("unshipped", html)  # the commit travels as a bubble

    def test_static_html_is_not_a_fake_live_dashboard(self):
        # Regression: a variable-shadowing bug once made every static --html
        # report render the live-mode banner and polling script.
        root = make_repo(self.base)
        out = os.path.join(self.base, "static.html")
        run_tool(["--no-net", "--html", out], cwd=root)
        with open(out) as f:
            html = f.read()
        self.assertNotIn("● live", html)
        self.assertNotIn("data.json", html)
        self.assertIn("snapshot", html)
        self.assertIn("re-run to refresh", html)

    def test_state_digest_stable_across_runs_when_nothing_changed(self):
        root = make_repo(self.base)
        d1 = json.loads(run_tool(["--no-net", "--json"], cwd=root).stdout)
        d2 = json.loads(run_tool(["--no-net", "--json"], cwd=root).stdout)
        self.assertEqual(d1["state_digest"], d2["state_digest"])
        self.assertNotEqual(d1["state_digest"], "")
        # and it changes when the repo changes
        with open(os.path.join(root, "new.txt"), "w") as f:
            f.write("x\n")
        d3 = json.loads(run_tool(["--no-net", "--json"], cwd=root).stdout)
        self.assertNotEqual(d1["state_digest"], d3["state_digest"])

    def test_component_dot_path_matches_everything(self):
        root = make_repo(self.base, commits=2)
        cfg = {"components": [{"name": "Everything", "paths": ["."]}]}
        with open(os.path.join(root, ".is-it-live.json"), "w") as f:
            json.dump(cfg, f)
        with open(os.path.join(root, "f0.txt"), "a") as f:
            f.write("dirty\n")
        data = json.loads(run_tool(["--no-net", "--json"], cwd=root).stdout)
        comp = data["components"][0]
        self.assertIsNotNone(comp["last"])
        # dirty f0.txt + untracked .is-it-live.json
        self.assertEqual(comp["dirty_files"], 2)
        self.assertEqual(comp["recent_commits"], 2)

    def test_component_literal_bracket_dir_matches_like_git(self):
        root = make_repo(self.base, commits=1)
        os.makedirs(os.path.join(root, "app", "[id]"))
        with open(os.path.join(root, "app", "[id]", "page.tsx"), "w") as f:
            f.write("x\n")
        git(["add", "."], cwd=root)
        git(["commit", "-m", "dynamic route"], cwd=root)
        cfg = {"components": [{"name": "Route", "paths": ["app/[id]"]}]}
        with open(os.path.join(root, ".is-it-live.json"), "w") as f:
            json.dump(cfg, f)
        data = json.loads(run_tool(["--no-net", "--json"], cwd=root).stdout)
        comp = data["components"][0]
        self.assertEqual(comp["last"]["subject"], "dynamic route")
        self.assertEqual(comp["recent_commits"], 1)

    def test_staged_rename_attributed_to_new_path(self):
        root = make_repo(self.base, commits=1)
        os.makedirs(os.path.join(root, "src"))
        os.makedirs(os.path.join(root, "lib"))
        with open(os.path.join(root, "src", "app.py"), "w") as f:
            f.write("x\n")
        with open(os.path.join(root, "lib", ".keep"), "w") as f:
            f.write("\n")
        git(["add", "."], cwd=root)
        git(["commit", "-m", "layout"], cwd=root)
        git(["mv", "src/app.py", "lib/app.py"], cwd=root)
        data = json.loads(run_tool(["--no-net", "--json"], cwd=root).stdout)
        self.assertIn("lib/app.py", data["staged"])
        self.assertNotIn("src/app.py -> lib/app.py", data["staged"])

    def test_non_http_version_url_is_refused_not_fetched(self):
        # SSRF / local-file guard: a repo-local config pointing a version source
        # at file:// must never be fetched — the scheme is rejected outright.
        root = make_repo(self.base, commits=1)
        secret = os.path.join(self.base, "secret.txt")
        with open(secret, "w") as f:
            f.write("TOPSECRET-DO-NOT-LEAK\n")
        cfg = {"version_sources": [{"label": "evil", "url": f"file://{secret}"}]}
        with open(os.path.join(root, ".is-it-live.json"), "w") as f:
            json.dump(cfg, f)
        r = run_tool(["--json"], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("TOPSECRET", r.stdout)
        data = json.loads(r.stdout)
        vr = data["live"]["version_results"][0]
        self.assertIsNone(vr["sha_raw"])
        self.assertEqual(vr["error"], "unreachable")

    def test_malicious_bundle_regex_does_not_crash(self):
        # An invalid/hostile bundle_regex from untrusted config must be swallowed,
        # not crash the run.
        root = make_repo(self.base, commits=1)
        cfg = {"production_urls": ["http://127.0.0.1:1/"], "bundle_regex": "("}
        with open(os.path.join(root, ".is-it-live.json"), "w") as f:
            json.dump(cfg, f)
        r = run_tool(["--json"], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        json.loads(r.stdout)  # still valid JSON, no crash

    def test_hostile_commit_subject_cannot_break_out_of_script(self):
        # The SVG journey payload embeds raw commit subjects inside <script>.
        # A subject containing </script or <!-- must be neutralized.
        root = make_repo(self.base, commits=1)
        with open(os.path.join(root, "x.txt"), "w") as f:
            f.write("x\n")
        git(["add", "."], cwd=root)
        git(["commit", "-m", "<!--</script><script>alert(1)</script> pwn"], cwd=root)
        out = os.path.join(self.base, "x.html")
        r = run_tool(["--no-net", "--html", out], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, encoding="utf-8") as f:
            html = f.read()
        # No raw closing tag and no raw "<" survived into the embedded JSON payload
        script_start = html.index("var J=")
        script_end = html.index("</script>", script_start)
        payload = html[script_start:script_end]
        self.assertNotIn("</script", payload.lower())
        self.assertNotIn("<!--", payload)
        self.assertIn("\\u003c", payload)  # the < was escaped

    def test_serve_dashboard_responds(self):
        import urllib.request
        root = make_repo(self.base)
        proc = subprocess.Popen(
            [sys.executable, "-u", TOOL, "--no-net", "--serve", "0", "--interval", "15"],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            url = None
            for _ in range(50):
                line = proc.stdout.readline()
                if "http://127.0.0.1:" in line:
                    url = line.split()[-1]
                    break
            self.assertTrue(url, "server never printed its URL")
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = resp.read().decode()
            self.assertIn("is-it-live", body)
            self.assertIn("● live", body)
            with urllib.request.urlopen(url + "data.json", timeout=30) as resp:
                data = json.loads(resp.read().decode())
            self.assertEqual(data["schema_version"], 2)
            self.assertIn("state_digest", data)
            # non-local Host headers are rejected (DNS-rebinding guard)
            req = urllib.request.Request(url, headers={"Host": "evil.example.com"})
            try:
                urllib.request.urlopen(req, timeout=30)
                self.fail("expected 403 for foreign Host header")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403)
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    # ---- --init / --html / --version ------------------------------------ #

    def test_init_scaffolds_config_and_refuses_overwrite(self):
        root = make_repo(self.base)
        r = run_tool(["--init"], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = os.path.join(root, ".is-it-live.json")
        with open(path) as f:
            cfg = json.load(f)
        self.assertIn("version_sources", cfg)
        r2 = run_tool(["--init"], cwd=root)
        self.assertEqual(r2.returncode, 1)
        self.assertIn("already exists", r2.stdout)

    def test_html_report_written_to_explicit_path(self):
        root = make_repo(self.base)
        out = os.path.join(self.base, "report.html")
        r = run_tool(["--no-net", "--html", out], cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out) as f:
            html = f.read()
        self.assertIn("ONE NEXT ACTION", html)
        self.assertIn("The Ladder", html)

    def test_version_flag(self):
        root = make_repo(self.base)
        r = run_tool(["--version"], cwd=root)
        self.assertEqual(r.returncode, 0)
        self.assertIn("is-it-live", r.stdout)

    def test_not_a_repo_exits_2(self):
        empty = os.path.join(self.base, "notrepo")
        os.makedirs(empty)
        r = run_tool(["--no-net"], cwd=empty)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
