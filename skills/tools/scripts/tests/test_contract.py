"""test-plan の品質契約と境界値に対応するテスト。"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import helpers
from test_logic import PNG, JPG, tool_line

import album_store
import art_store
import cards as card_builder
import launcher
import scan
import server as server_module
import text_store
import usage

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(os.path.dirname(SCRIPTS), "web")
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; media-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def harness(self):
        h = helpers.Harness(self.root)
        self.addCleanup(h.close)
        return h


class HostOriginTest(Base):
    def test_every_bad_host_and_origin(self):
        h = self.harness()
        for host in ("evil.example", "localhost:%d" % h.port, "127.0.0.1:9999", ""):
            self.assertEqual(h.raw("GET", "/api/ping", headers={"Host": host})[0], 403, host)
        origin = {"Origin": "http://evil.example", "Content-Length": "0"}
        self.assertEqual(h.raw("POST", "/api/shutdown", headers=origin)[0], 403)
        self.assertEqual(h.raw("PUT", "/api/album", b"{}", {"Origin": "null", "Content-Type": "application/json"})[0], 403)
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", h.port, timeout=5)
        conn.request("POST", "/api/shutdown", headers={"Content-Length": "0"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_binds_loopback_only(self):
        h = self.harness()
        self.assertEqual(h.server.server_address[0], "127.0.0.1")


class HeadersTest(Base):
    def test_all_responses_have_exact_headers(self):
        h = self.harness()
        cases = [("GET", "/"), ("GET", "/a.js"), ("GET", "/api/ping"), ("GET", "/nope.js"), ("GET", "/api/nope")]
        for method, path in cases:
            headers = h.raw(method, path)[2]
            self.assertEqual(headers["Content-Security-Policy"], CSP, path)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff", path)
            self.assertEqual(headers["Cache-Control"], "no-store", path)
        self.assertEqual(h.raw("GET", "/", headers={"Host": "x"})[2]["Content-Security-Policy"], CSP)

    def test_evil_body_is_json(self):
        h = self.harness()
        evil = "<script>alert(1)</script><img src=x onerror=alert(1)> javascript:alert(1) ![](https://evil.example/x.png)"
        helpers.write(os.path.join(h.home, "skills", "evil", "SKILL.md"), helpers.skill("evil", "説明 <script>alert(1)</script>") + evil)
        h.app.refresh(force=True)
        status, body, headers = h.raw("GET", "/api/cards/evil")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("<script>", json.loads(body)["body"])


class StaticSafetyTest(unittest.TestCase):
    def read_web(self, *names):
        text = {}
        for name in names:
            with open(os.path.join(WEB, name), encoding="utf-8") as fh:
                text[name] = fh.read()
        return text

    def test_js_has_no_html_injection(self):
        import re

        pattern = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function|cssText|setAttribute\(['\"]style")
        for name, text in self.read_web("album.js", "card.js", "holo.js", "sound.js").items():
            self.assertIsNone(pattern.search(text), name)

    def test_vendor_page_flip_is_pinned(self):
        import hashlib

        with open(os.path.join(WEB, "vendor", "page-flip.browser.js"), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(digest, "bbaca0bbef57a22bb66a3fc69d67baf9a17fb9a9c89ec9ed35e2b91abe4bd1e7")
        self.assertTrue(os.path.isfile(os.path.join(WEB, "vendor", "page-flip.LICENSE.txt")))

    def test_html_has_no_inline(self):
        import re

        html = self.read_web("index.html")["index.html"]
        self.assertIsNone(re.search(r"<style|style=|<script[^>]*>[^<]|\son\w+=|https?://", html))

    def test_no_shell_true(self):
        for name in os.listdir(SCRIPTS):
            if name.endswith(".py"):
                with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
                    self.assertNotIn("shell=True", fh.read(), name)

    def test_writes_target_data_dir_only(self):
        for name in ("scan.py", "frontmatter.py", "cards.py"):
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
                text = fh.read()
            for word in ("os.replace", "save_", '"w"', '"a"', "shutil."):
                self.assertNotIn(word, text, name)


class PathTest(Base):
    def test_paths_and_params(self):
        h = self.harness()
        helpers.write(os.path.join(h.home, ".env", "x"), "SENTINEL-ENV-789")
        for path in (
            "/api/cards/..%2F..%2F.env",
            "/api/cards/%2e%2e%2f.env",
            "/api/cards/..\\..\\.env",
            "/api/cards/..%2F.env/art",
            "/api/cards/C:%5CUsers/art",
            "/../server.py",
            "/%2e%2e/server.py",
            "/assets/..%2f..%2fscripts%2fserver.py",
            "/x.py",
            "/x.json",
            "/.env",
            "/x.md",
        ):
            status, body, _ = h.raw("GET", path)
            self.assertIn(status, (400, 403, 404), path)
            self.assertNotIn(b"SENTINEL", body, path)
        for bad in ("a" * 35, "a" * 37, "../x", "%0A", "", helpers.SESSION + "%0A"):
            self.assertEqual(h.json("POST", "/api/cards/alpha/use?s=%s" % bad)[0], 400, bad)
        for seq in ("0", "-1", "1.5", "abc", str(2 ** 53)):
            self.assertEqual(h.json("GET", "/api/use-result?s=%s&seq=%s" % (helpers.SESSION, seq))[0], 400, seq)

    def test_open_refuses_non_dir(self):
        h = self.harness()
        card = h.app.card_index()["alpha"]
        with mock.patch.object(server_module.os.path, "isdir", return_value=False):
            self.assertEqual(h.json("POST", "/api/cards/alpha/open")[0], 404)
        self.assertEqual(h.opened, [])
        self.assertTrue(card["dir"].endswith("alpha"))

    def test_symlink_out_of_root_is_dropped(self):
        home = helpers.build_home(self.root)
        outside = os.path.join(self.root, "outside")
        helpers.write(os.path.join(outside, "SKILL.md"), helpers.skill("escaped"))
        link = os.path.join(home, "skills", "linked")
        try:
            subprocess.run(["cmd", "/c", "mklink", "/J", link, outside], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("junction を作れない")
        ids = [c["id"] for c in scan.scan(home, os.path.join(self.root, ".claude.json"))["cards"]]
        self.assertNotIn("escaped", ids)
        self.assertNotIn("linked", ids)

    def test_bad_names_never_become_ids(self):
        home = helpers.build_home(self.root)
        for index, name in enumerate(("a b", "a/b", "..", "x" * 65)):
            helpers.write(os.path.join(home, "skills", "safe%d" % index, "SKILL.md"), helpers.skill(name))
        ids = [c["id"] for c in scan.scan(home, os.path.join(self.root, ".claude.json"))["cards"]]
        for index in range(4):
            self.assertIn("safe%d" % index, ids)

    def test_server_rejects_unknown_session_argument(self):
        out = subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, "server.py"), "--session", "../x"], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 2)


class ScanEdgeTest(Base):
    def test_excluded_and_collision(self):
        home = helpers.build_home(self.root)
        helpers.write(os.path.join(home, "plugins", "synced", "x", "figma", "skills", "use", "SKILL.md"), helpers.skill())
        helpers.write(os.path.join(home, "plugins", ".trash", "t", "skills", "old", "SKILL.md"), helpers.skill())
        helpers.write(os.path.join(home, "local-marketplace", "plugins", "w", "skills", "w", "SKILL.md"), helpers.skill())
        helpers.write(os.path.join(home, "skills", "alpha", "deep", "nested", "SKILL.md"), helpers.skill("nested"))
        result = scan.scan(home, os.path.join(self.root, ".claude.json"))
        ids = {c["id"] for c in result["cards"]}
        self.assertEqual(ids, {"alpha", "beta-name", "anthropic-skills:pdf", "git-ops:commit", "wiki:wiki"})

    def test_duplicate_id_prefers_user(self):
        home = helpers.build_home(self.root)
        helpers.write(os.path.join(home, "skills", "other", "SKILL.md"), helpers.skill("alpha", "二枚目"))
        result = scan.scan(home, os.path.join(self.root, ".claude.json"))
        self.assertEqual(len([c for c in result["cards"] if c["id"] == "alpha"]), 1)
        self.assertIn("duplicate-id", [d["kind"] for d in result["diagnostics"]])

    def test_user_and_plugin_same_name_are_separate(self):
        home = helpers.build_home(self.root)
        helpers.write(os.path.join(home, "skills", "commit", "SKILL.md"), helpers.skill())
        ids = {c["id"] for c in scan.scan(home, os.path.join(self.root, ".claude.json"))["cards"]}
        self.assertTrue({"commit", "git-ops:commit"} <= ids)

    def test_file_list_limit(self):
        home = helpers.build_home(self.root)
        for index in range(201):
            helpers.write(os.path.join(home, "skills", "many", "f%03d.txt" % index), "x")
        helpers.write(os.path.join(home, "skills", "many", "SKILL.md"), helpers.skill())
        card = [c for c in scan.scan(home, os.path.join(self.root, ".claude.json"))["cards"] if c["id"] == "many"][0]
        self.assertEqual(len(card["files"]), 200)

    def test_empty_skill_file(self):
        home = helpers.build_home(self.root)
        helpers.write(os.path.join(home, "skills", "empty", "SKILL.md"), "")
        card = [c for c in scan.scan(home, os.path.join(self.root, ".claude.json"))["cards"] if c["id"] == "empty"][0]
        self.assertEqual(card["description"], "")
        self.assertEqual(card_builder.cost_of(card["chars"]), 1)


class InitialClassificationTest(Base):
    def test_initial_new_mark_and_numbers(self):
        h = self.harness()
        cards = h.cards()
        self.assertEqual(cards["git-ops:commit"]["folder"], "uncategorized")
        self.assertEqual(cards["anthropic-skills:pdf"]["folder"], "uncategorized")
        self.assertEqual(cards["alpha"]["folder"], "uncategorized")
        self.assertTrue(all(c["firstSeen"] is None for c in cards.values()))
        ordered = sorted(cards, key=lambda i: cards[i]["no"])
        self.assertEqual(ordered, sorted(ordered))
        helpers.write(os.path.join(h.home, "skills", "zz-new", "SKILL.md"), helpers.skill())
        h.app.refresh(force=True)
        added = h.cards()["zz-new"]
        self.assertIsNotNone(added["firstSeen"])
        self.assertEqual(added["folder"], "uncategorized")
        self.assertEqual(added["no"], len(cards) + 1)


class ForcedKillTest(Base):
    CHILD = """
import os, sys
sys.path.insert(0, {scripts!r})
import atomic
real = os.replace
def stop(a, b):
    os._exit(9)
if {kill!r}:
    os.replace = stop
if {which!r} == "album":
    import album_store
    store = album_store.AlbumStore({data!r})
    store.update({{"revision": store.data["revision"], "sound": False}})
else:
    import art_store
    art_store.ArtStore({data!r}).put("a", {jpg!r})
"""

    def run_child(self, which):
        code = self.CHILD.format(scripts=SCRIPTS, kill=True, which=which, data=self.root, jpg=JPG)
        return subprocess.run([sys.executable, "-I", "-c", code], timeout=30).returncode

    def test_album_survives_kill(self):
        store = album_store.AlbumStore(self.root)
        store.sync({"cards": [{"id": "a", "source": "user"}], "counts": {"user": 1}, "diagnostics": []})
        self.assertEqual(self.run_child("album"), 9)
        again = album_store.AlbumStore(self.root)
        self.assertIsNone(again.notice)
        self.assertIn("a", again.data["cards"])
        self.assertTrue(again.data["sound"])

    def test_art_survives_kill(self):
        art_store.ArtStore(self.root).put("a", PNG)
        self.assertEqual(self.run_child("art"), 9)
        self.assertEqual(art_store.ArtStore(self.root).read("a")[0], PNG)

    def test_bak_only_from_valid_content(self):
        store = album_store.AlbumStore(self.root)
        store.sync({"cards": [{"id": "a", "source": "user"}], "counts": {"user": 1}, "diagnostics": []})
        with open(store.bak, encoding="utf-8") as fh:
            before = fh.read()
        with self.assertRaises(album_store.StoreError):
            store.update({"revision": store.data["revision"], "cards": {"a": {"folder": "none"}}})
        with open(store.bak, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)


class WhyReviewRegressionTest(Base):
    def album(self):
        store = album_store.AlbumStore(self.root)
        store.sync({"cards": [{"id": "a", "source": "user"}], "counts": {"user": 1}, "diagnostics": []})
        return store

    def test_read_failure_stops_instead_of_resetting(self):
        store = self.album()
        with open(store.path, "rb") as fh:
            before = fh.read()
        real = open

        def deny(path, *args, **kwargs):
            if str(path).endswith("album.json"):
                raise PermissionError("locked")
            return real(path, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=deny):
            with self.assertRaises(PermissionError):
                album_store.AlbumStore(self.root)
        with open(store.path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_missing_main_uses_bak(self):
        store = self.album()
        os.remove(store.path)
        again = album_store.AlbumStore(self.root)
        self.assertEqual(again.notice, "restored")
        self.assertIn("a", again.data["cards"])

    def test_broken_without_backup_copy_does_not_reset(self):
        store = self.album()
        with open(store.path, "w") as fh:
            fh.write("{broken")
        with open(store.bak, "w") as fh:
            fh.write("{broken")
        with mock.patch("album_store.shutil.copyfile", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                album_store.AlbumStore(self.root)
        with open(store.path) as fh:
            self.assertEqual(fh.read(), "{broken")

    def test_failed_save_leaves_memory_unchanged(self):
        store = self.album()
        revision = store.data["revision"]
        with mock.patch("album_store.save_json", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                store.update({"revision": revision, "sound": False})
            with self.assertRaises(OSError):
                store.auto_assign({"a": "dev"})
        self.assertEqual(store.data["revision"], revision)
        self.assertTrue(store.data["sound"])
        self.assertEqual(store.data["cards"]["a"]["folder"], "uncategorized")

    def test_sync_does_not_change_revision(self):
        store = self.album()
        revision = store.data["revision"]
        store.sync({"cards": [{"id": "a", "source": "user"}, {"id": "b", "source": "user"}], "counts": {"user": 2}, "diagnostics": []})
        self.assertEqual(store.data["revision"], revision)
        self.assertIn("b", store.data["cards"])

    def test_get_does_not_write_album(self):
        h = self.harness()
        helpers.write(os.path.join(h.home, "skills", "zz-new", "SKILL.md"), helpers.skill())
        before = os.stat(h.app.album.path).st_mtime_ns
        for path in ("/api/cards", "/api/album", "/api/state", "/api/usage", "/api/export?kind=classify"):
            self.assertEqual(h.raw("GET", path)[0], 200, path)
        self.assertEqual(os.stat(h.app.album.path).st_mtime_ns, before)
        self.assertNotIn("zz-new", h.json("GET", "/api/album")[1]["cards"])

    def test_foreign_origin_get_is_refused(self):
        h = self.harness()
        self.assertEqual(h.raw("GET", "/api/cards", headers={"Origin": "https://evil.example"})[0], 403)

    def test_new_card_does_not_conflict_with_edit(self):
        h = self.harness()
        revision = h.json("GET", "/api/cards")[1]["revision"]
        rev_before = h.json("GET", "/api/state")[1]["cardsRev"]
        helpers.write(os.path.join(h.home, "skills", "zz-new", "SKILL.md"), helpers.skill())
        h.app.refresh()
        self.assertEqual(h.json("PUT", "/api/album", {"revision": revision, "cards": {"alpha": {"folder": "git"}}})[0], 200)
        self.assertGreater(h.json("GET", "/api/state")[1]["cardsRev"], rev_before)

    def test_one_bad_card_keeps_the_rest(self):
        home = helpers.build_home(self.root)
        helpers.write(os.path.join(home, "skills", "zz-last", "SKILL.md"), helpers.skill())
        real = scan._make_card

        def flaky(folder, *args, **kwargs):
            if folder.endswith("beta"):
                raise OSError("locked")
            return real(folder, *args, **kwargs)

        with mock.patch("scan._make_card", side_effect=flaky):
            result = scan.scan(home, os.path.join(self.root, ".claude.json"))
        ids = [c["id"] for c in result["cards"]]
        self.assertIn("zz-last", ids)
        self.assertIn("card-error", [d["kind"] for d in result["diagnostics"]])

    def test_list_failure_keeps_card(self):
        home = helpers.build_home(self.root)
        real = os.listdir

        def flaky(path="."):
            if str(path).endswith("scripts"):
                raise PermissionError()
            return real(path)

        with mock.patch("scan.os.listdir", side_effect=flaky):
            result = scan.scan(home, os.path.join(self.root, ".claude.json"))
        ids = [c["id"] for c in result["cards"]]
        self.assertIn("alpha", ids)
        self.assertIn("wiki:wiki", ids)

    def test_other_org_is_not_shown(self):
        home = helpers.build_home(self.root)
        dot = os.path.join(self.root, ".claude.json")
        helpers.write(dot, json.dumps({"oauthAccount": {"organizationUuid": "cccccccc-0000-0000-0000-000000000000"}}))
        result = scan.scan(home, dot)
        self.assertFalse([c for c in result["cards"] if c["kind"] == "公式"])

    def tracker(self, projects):
        return usage.UsageTracker(projects, os.path.join(self.root, "data"))

    def test_unknown_format_stays_unreadable_on_next_start(self):
        projects = os.path.join(self.root, "projects")
        helpers.write(os.path.join(projects, "p", "a.jsonl"), "\n".join(json.dumps({"e": i}) for i in range(25)) + "\n")
        for _ in range(2):
            tracker = self.tracker(projects)
            tracker.scan()
            self.assertEqual(tracker.state, "unreadable")

    def test_renamed_tool_is_unreadable_on_every_start(self):
        projects = os.path.join(self.root, "projects")
        line = json.dumps({"message": {"content": [{"type": "tool_use", "id": "t", "name": "SkillV2", "input": {"skill": "a"}}]}})
        helpers.write(os.path.join(projects, "p", "a.jsonl"), "\n".join([line] * 25) + "\n")
        for _ in range(2):
            tracker = self.tracker(projects)
            tracker.scan()
            self.assertEqual(tracker.state, "unreadable")

    def test_missing_projects_is_unreadable(self):
        tracker = self.tracker(os.path.join(self.root, "nothing"))
        tracker.scan()
        self.assertEqual(tracker.state, "unreadable")

    def test_ledger_survives_missing_history(self):
        projects = os.path.join(self.root, "projects")
        path = os.path.join(projects, "p", "a.jsonl")
        helpers.write(path, tool_line("t1", "a") + "\n")
        self.tracker(projects).scan()
        shutil.rmtree(projects)
        tracker = self.tracker(projects)
        tracker.scan()
        self.assertEqual(tracker.state, "ok")
        self.assertEqual(tracker.summary()["counts"], {"a": 1})

    def test_scan_save_failure_is_not_unreadable(self):
        projects = os.path.join(self.root, "projects")
        helpers.write(os.path.join(projects, "p", "a.jsonl"), tool_line("t1", "a") + "\n")
        tracker = self.tracker(projects)
        with mock.patch("usage.save_json", side_effect=OSError("disk")):
            tracker.scan()
        self.assertEqual(tracker.state, "ok")

    def test_restore_failure_rolls_back(self):
        store = art_store.ArtStore(self.root)
        store.put("a", PNG)
        store.put("a", JPG)
        real = art_store.replace_with_retry
        calls = []

        def flaky(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError("crash")
            real(source, target)

        with mock.patch("art_store.replace_with_retry", side_effect=flaky):
            with self.assertRaises(OSError):
                store.restore("a")
        self.assertEqual(store.read("a")[0], JPG)
        store.restore("a")
        self.assertEqual(store.read("a")[0], PNG)

    def test_swap_left_by_crash_is_recovered(self):
        store = art_store.ArtStore(self.root)
        store.put("a", PNG)
        current = store._find(store.art, "a")
        os.replace(current, current + ".swap")
        helpers.write(os.path.join(store.art, "b.png.new"), "partial")
        again = art_store.ArtStore(self.root)
        self.assertEqual(again.read("a")[0], PNG)
        self.assertFalse(os.path.exists(os.path.join(store.art, "b.png.new")))

    def test_replace_keeps_current_until_new_is_in_place(self):
        store = art_store.ArtStore(self.root)
        store.put("a", PNG)
        with mock.patch("art_store.replace_with_retry", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                store.put("a", JPG)
        self.assertEqual(art_store.ArtStore(self.root).read("a")[0], PNG)

    def test_ping_handles_broken_servers(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)

        def answer():
            conn, _ = listener.accept()
            conn.recv(1024)
            conn.sendall(b"NOT HTTP\r\n\r\n")
            conn.close()

        threading.Thread(target=answer, daemon=True).start()
        self.assertEqual(launcher.ping(listener.getsockname()[1]), "other")

    def test_failed_start_is_not_port_busy(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        with mock.patch.object(launcher, "spawn"):
            self.assertEqual(launcher.ensure(port, self.root, wait=0.05), "failed")


class ConcurrentBindTest(Base):
    def test_only_one_binds(self):
        home = helpers.build_home(self.root)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        data = os.path.join(self.root, "data")
        procs = [
            subprocess.Popen([sys.executable, "-I", os.path.join(SCRIPTS, "server.py"), "--port", str(port), "--claude-home", home, "--data-dir", data], stderr=subprocess.DEVNULL)
            for _ in range(5)
        ]
        try:
            time.sleep(6)
            alive = [p for p in procs if p.poll() is None]
            self.assertEqual(len(alive), 1)
            self.assertEqual(launcher.ping(port), "ours")
            log = os.path.join(data, "server.log")
            before = os.stat(log).st_mtime_ns
            ledger = os.path.join(data, "usage-events.jsonl")
            helpers.write(ledger, '{"id":"x","skill":"a","ts":""}\n{"id":"cut')
            loser = subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, "server.py"), "--port", str(port), "--claude-home", home, "--data-dir", data], capture_output=True, timeout=15)
            self.assertEqual(loser.returncode, 3)
            self.assertEqual(os.stat(log).st_mtime_ns, before)
            with open(ledger, encoding="utf-8") as fh:
                self.assertTrue(fh.read().endswith('{"id":"cut'))
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(5)


class LifecycleTest(Base):
    def test_idle_shutdown_and_sleep_reset(self):
        h = self.harness()
        calls = []
        h.app.server = mock.Mock(shutdown=lambda: calls.append(1))
        h.app.idle = 600
        h.app.last_access = 1000.0
        moments = [1000.0, 1005.0, 1100.0, 1105.0]
        last = [1105.0]
        seen_access = []

        class Clock:
            def time(self):
                if moments:
                    return moments.pop(0)
                last[0] += 20
                return last[0]

            def __getattr__(self, name):
                return getattr(time, name)

        class Stop:
            def wait(self, _):
                seen_access.append(h.app.last_access)
                return False

        real = server_module.time
        server_module.time = Clock()
        try:
            h.app.watch(Stop())
        finally:
            server_module.time = real
        time.sleep(0.1)
        self.assertEqual(calls, [1])
        self.assertEqual(seen_access[2:4], [1100.0, 1100.0])
        h.app.server = h.server

    def test_clean_handoff(self):
        h = self.harness()
        old = os.path.join(h.data, "inbox", "old.json")
        fresh = os.path.join(h.data, "outbox", "fresh.json")
        helpers.write(old, "{}")
        helpers.write(fresh, "{}")
        stamp = time.time() - 8 * 86400
        os.utime(old, (stamp, stamp))
        h.app.clean_handoff()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

    def test_state_shape(self):
        h = self.harness()
        self.assertEqual(set(h.json("GET", "/api/state")[1]), {"version", "albumRevision", "cardsRev", "textsRev", "artRev", "usage"})

    def test_old_version_is_replaced(self):
        h = self.harness()
        with mock.patch.object(launcher, "VERSION", "0"), mock.patch.object(launcher, "spawn") as spawn, mock.patch.object(launcher, "wait_for", return_value="ours"):
            self.assertEqual(launcher.ensure(h.port, h.data), "ok")
        spawn.assert_called_once()
        deadline = time.time() + 5
        while time.time() < deadline and launcher.ping(h.port) != "none":
            time.sleep(0.05)
        self.assertEqual(launcher.ping(h.port), "none")
        h.close = lambda: None
        h.server.server_close()

    def test_port_held_by_other_is_busy(self):
        holder = socket.socket()
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        with mock.patch.object(launcher, "spawn"), mock.patch.object(launcher, "ping", side_effect=["none", "none"]):
            self.assertEqual(launcher.ensure(holder.getsockname()[1], self.root, wait=0.05), "port-busy")


class UseIsolationTest(Base):
    def test_session_files_are_separate(self):
        h = self.harness()
        other = "99999999-2222-3333-4444-555555555555"
        seq = h.json("POST", "/api/cards/alpha/use?s=%s" % helpers.SESSION)[1]["seq"]
        self.assertEqual(os.listdir(os.path.join(h.data, "inbox")), [helpers.SESSION + ".json"])
        helpers.write(os.path.join(h.data, "outbox", helpers.SESSION + ".json"), json.dumps({"seq": seq, "isFilled": True}))
        self.assertEqual(h.json("GET", "/api/use-result?s=%s&seq=%d" % (other, seq))[1]["state"], "pending")
        self.assertEqual(h.json("POST", "/api/cards/alpha/use")[0], 400)


class IfAbsentRaceTest(Base):
    def test_two_clients_one_winner(self):
        h = self.harness()
        results = []

        def send(data, kind):
            results.append(h.raw("PUT", "/api/cards/alpha/art?ifAbsent=1", data, {"Content-Type": kind})[0])

        threads = [threading.Thread(target=send, args=(PNG, "image/png")), threading.Thread(target=send, args=(JPG, "image/jpeg"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), [200, 409])
        self.assertFalse(h.cards()["alpha"]["hasPrev"])

    def test_existing_art_and_prev_unchanged(self):
        h = self.harness()
        h.raw("PUT", "/api/cards/alpha/art", PNG, {"Content-Type": "image/png"})
        status, body, _ = h.raw("PUT", "/api/cards/alpha/art?ifAbsent=1", JPG, {"Content-Type": "image/jpeg"})
        self.assertEqual((status, json.loads(body)["reason"]), (409, "exists"))
        self.assertEqual(h.raw("GET", "/api/cards/alpha/art")[1], PNG)
        self.assertFalse(h.cards()["alpha"]["hasPrev"])

    def test_readonly_is_not_exists(self):
        h = self.harness()
        h.app.album.readonly = True
        status, body, _ = h.raw("PUT", "/api/cards/alpha/art?ifAbsent=1", PNG, {"Content-Type": "image/png"})
        self.assertEqual((status, json.loads(body)["reason"]), (409, "readonly"))


class ArtEdgeTest(Base):
    def test_bad_requests_keep_art(self):
        h = self.harness()
        h.raw("PUT", "/api/cards/alpha/art", PNG, {"Content-Type": "image/png"})
        before = sorted(os.listdir(os.path.join(h.data, "art")))
        for data, kind in ((b"<svg/>", "image/png"), (JPG[:2], "image/jpeg"), (b"<html>", "text/html")):
            self.assertIn(h.raw("PUT", "/api/cards/alpha/art", data, {"Content-Type": kind})[0], (400, 415))
        import http.client

        for value in (None, "-1", "abc"):
            conn = http.client.HTTPConnection("127.0.0.1", h.port, timeout=5)
            conn.putrequest("PUT", "/api/cards/alpha/art")
            conn.putheader("Origin", "http://127.0.0.1:%d" % h.port)
            conn.putheader("Content-Type", "image/png")
            if value:
                conn.putheader("Content-Length", value)
            conn.endheaders()
            self.assertIn(conn.getresponse().status, (400, 411))
            conn.close()
        self.assertEqual(sorted(os.listdir(os.path.join(h.data, "art"))), before)
        self.assertEqual(h.raw("GET", "/api/cards/alpha/art")[1], PNG)

    def test_filenames_stay_inside(self):
        for card_id in ("../x", "..\\x", "a:b:c", "CON", "nul", "COM1"):
            name = art_store.art_filename(card_id)
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)
            self.assertNotIn(":", name)
            self.assertNotIn(name.upper(), art_store.RESERVED)

    def test_exactly_ten_megabytes(self):
        store = art_store.ArtStore(self.root)
        store.put("a", PNG + b"0" * (art_store.MAX_BYTES - len(PNG)))
        with self.assertRaises(art_store.ArtError):
            store.put("b", PNG + b"0" * (art_store.MAX_BYTES - len(PNG) + 1))


class TextEdgeTest(Base):
    CARD = {"a": {"id": "a", "sourceHash": "h", "description": ""}}

    def test_boundaries(self):
        store = text_store.TextStore(self.root)
        ok = {"summary": "あ" * 80, "flavor": "あ" * 40, "sourceHash": "h"}
        self.assertEqual(store.apply_texts({"a": ok}, self.CARD)["applied"], 1)
        self.assertEqual(store.apply_texts({"a": dict(ok, summary="あ" * 81)}, self.CARD)["applied"], 0)
        self.assertEqual(store.apply_texts({"a": dict(ok, flavor="あ" * 41)}, self.CARD)["applied"], 0)
        self.assertEqual(store.apply_motifs({"a": "あ" * 120}, self.CARD)["applied"], 1)
        self.assertEqual(store.apply_motifs({"a": "あ" * 121}, self.CARD)["applied"], 0)

    def test_prompt_markers(self):
        h = self.harness()
        marker = "DESC-MARKER-" + "A" * 20
        helpers.write(os.path.join(h.home, "skills", "mark", "SKILL.md"), helpers.skill("mark", marker) + "BODY-MARKER C:\\Users\\x\n")
        h.app.refresh(force=True)
        result = h.json("POST", "/api/motifs", {"mark": marker[:24], "alpha": "光る虫眼鏡", "zzz": "鍵"})[1]
        self.assertEqual(result["applied"], 1)
        self.assertEqual({r["reason"] for r in result["rejected"]}, {"copied", "unknown-card"})
        with open(os.path.join(h.data, "art-prompts.md"), encoding="utf-8") as fh:
            text = fh.read()
        for word in ("DESC-MARKER", "BODY-MARKER", "C:\\", "SKILL.md", h.home):
            self.assertNotIn(word, text)
        self.assertIn("_alpha.png", text)
        h.raw("PUT", "/api/cards/alpha/art", PNG, {"Content-Type": "image/png"})
        h.json("POST", "/api/motifs", {})
        with open(os.path.join(h.data, "art-prompts.md"), encoding="utf-8") as fh:
            self.assertNotIn("alpha", fh.read())

    def test_agent_definition(self):
        profile = os.environ.get("USERPROFILE")
        path = os.path.join(profile, ".claude", "agents", "skill-album-card-writer.md")
        with open(path, encoding="utf-8") as fh:
            head = fh.read().split("---")[1]
        self.assertIn("tools: Read\n", head)
        skill_path = os.path.join(os.path.dirname(SCRIPTS), "SKILL.md")
        with open(skill_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("メインスレッドで SKILL.md を読まない", text)
        self.assertIn("disable-model-invocation: true", text)


class AlbumApiTest(Base):
    def test_server_fields_are_ignored(self):
        h = self.harness()
        h.cards()
        album = h.json("GET", "/api/album")[1]
        before = album["cards"]["alpha"]
        status, saved = h.json("PUT", "/api/album", {"revision": album["revision"], "cards": {"alpha": {"no": 999, "firstSeen": "x", "by": "manual"}}})
        self.assertEqual(status, 200)
        self.assertEqual(saved["cards"]["alpha"], before)

    def test_field_limits(self):
        h = self.harness()
        h.cards()
        album = h.json("GET", "/api/album")[1]
        rev = album["revision"]
        folders = album["folders"]
        self.assertEqual(h.json("PUT", "/api/album", {"revision": rev, "folders": [dict(folders[0], color="#GGGGGG")] + folders[1:]})[0], 400)
        many = folders + [{"id": "n%d" % i, "name": "x", "color": "#000000", "order": 0} for i in range(16 - len(folders))]
        status, saved = h.json("PUT", "/api/album", {"revision": rev, "folders": many})
        self.assertEqual(status, 200)
        self.assertEqual(h.json("PUT", "/api/album", {"revision": saved["revision"], "folders": many + [{"id": "n99", "name": "x", "color": "#000000", "order": 0}]})[0], 400)

    def test_rejected_returns_no_input(self):
        h = self.harness()
        result = h.json("POST", "/api/auto-assign", {"zzz-unknown": "dev", "alpha": "<none>"})[1]
        self.assertNotIn("<none>", json.dumps(result))
        self.assertEqual(sorted(r["reason"] for r in result["rejected"]), ["unknown-card", "unknown-folder"])


class SkillUntouchedTest(Base):
    def test_all_routes_keep_home_intact(self):
        h = self.harness()
        sums = {}

        def snapshot():
            out = {}
            for base, _, files in os.walk(h.home):
                for name in files:
                    path = os.path.join(base, name)
                    stat = os.stat(path)
                    with open(path, "rb") as fh:
                        out[path] = (hashlib.sha256(fh.read()).hexdigest(), stat.st_mtime)
            return out

        sums = snapshot()
        for path in ("/api/cards", "/api/album", "/api/usage", "/api/state", "/api/cards/alpha", "/api/export?kind=texts", "/api/export?kind=classify", "/api/export?kind=prompts"):
            h.raw("GET", path)
        h.json("POST", "/api/cards/alpha/open")
        h.json("POST", "/api/auto-assign", {"alpha": "dev"})
        index = h.app.card_index()
        h.json("POST", "/api/texts", {"alpha": {"summary": "要約", "flavor": "一言", "sourceHash": index["alpha"]["sourceHash"]}})
        h.json("POST", "/api/motifs", {"alpha": "鍵"})
        h.raw("PUT", "/api/cards/alpha/art", PNG, {"Content-Type": "image/png"})
        h.app.usage.scan()
        self.assertEqual(snapshot(), sums)


class UsageContractTest(Base):
    def setUp(self):
        super().setUp()
        self.projects = os.path.join(self.root, "projects")
        self.data = os.path.join(self.root, "data")

    def tracker(self):
        return usage.UsageTracker(self.projects, self.data)

    def test_secrets_never_stored(self):
        line = json.dumps({"timestamp": "2026-11-01T00:00:00Z", "message": {"content": [
            {"type": "text", "text": "SECRET-BODY-456"},
            {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "a", "args": "SECRET-ARGS-123"}},
        ]}})
        helpers.write(os.path.join(self.projects, "p", "a.jsonl"), line + "\n")
        tracker = self.tracker()
        tracker.scan()
        for name in os.listdir(self.data):
            with open(os.path.join(self.data, name), encoding="utf-8") as fh:
                self.assertNotIn("SECRET", fh.read(), name)
        event = next(iter(tracker.events.values()))
        self.assertEqual(set(event), {"id", "skill", "ts"})
        self.assertEqual(event["id"], hashlib.sha256(b"t1").hexdigest()[:16])

    def test_nineteen_lines_is_not_unreadable(self):
        helpers.write(os.path.join(self.projects, "p", "a.jsonl"), "\n".join(json.dumps({"e": i}) for i in range(19)) + "\n")
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.state, "ok")

    def test_unreadable_marks_all_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            h = helpers.Harness(root)
            self.addCleanup(h.close)
            helpers.write(os.path.join(h.home, "projects", "p", "a.jsonl"), "\n".join(json.dumps({"e": i}) for i in range(25)) + "\n")
            h.app.usage.scan()
            self.assertEqual(h.json("GET", "/api/usage")[1]["state"], "unreadable")
            cards = h.cards()
            self.assertTrue(all(c["count"] is None and c["rarity"] == 1 for c in cards.values()))

    def test_robust_files(self):
        path = os.path.join(self.projects, "p")
        helpers.write(os.path.join(path, "empty.jsonl"), "")
        helpers.write(os.path.join(path, "cut.jsonl"), tool_line("t1", "a") + "\n" + tool_line("t2", "a")[:30])
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "bin.jsonl"), "wb") as fh:
            fh.write(b"\xff\xfe\x00\n" + tool_line("t3", "b").encode() + b"\n")
        helpers.write(os.path.join(path, "huge.jsonl"), json.dumps({"message": {"content": "x" * 5000000}}) + "\n")
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.summary()["counts"], {"a": 1, "b": 1})

    def test_version_mismatch_rescans_same_result(self):
        helpers.write(os.path.join(self.projects, "p", "a.jsonl"), tool_line("t1", "a") + "\n")
        self.tracker().scan()
        scan_path = os.path.join(self.data, "usage-scan.json")
        with open(scan_path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["version"] = 0
        helpers.write(scan_path, json.dumps(data))
        tracker = self.tracker()
        self.assertEqual(tracker.scan_info["files"], {})
        tracker.scan()
        self.assertEqual(tracker.summary()["counts"], {"a": 1})

    def test_killed_during_scan_does_not_double_count(self):
        helpers.write(os.path.join(self.projects, "p", "a.jsonl"), tool_line("t1", "a") + "\n")
        helpers.write(os.path.join(self.projects, "p", "b.jsonl"), tool_line("t2", "a") + "\n")
        first = self.tracker()
        first.scan()
        os.remove(first.scan_path)
        again = self.tracker()
        again.scan()
        self.assertEqual(again.summary()["counts"], {"a": 2})

    def test_unknown_skills_stay_in_ledger(self):
        helpers.write(os.path.join(self.projects, "p", "a.jsonl"), tool_line("t1", "removed-skill") + "\n")
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.summary()["counts"]["removed-skill"], 1)


class HeaderBoundaryTest(Base):
    def test_content_length_forms(self):
        h = self.harness()
        import http.client

        for values in (["1e3"], ["-1"], ["abc"], ["0", "0"]):
            conn = http.client.HTTPConnection("127.0.0.1", h.port, timeout=5)
            conn.putrequest("POST", "/api/auto-assign")
            conn.putheader("Origin", "http://127.0.0.1:%d" % h.port)
            for value in values:
                conn.putheader("Content-Length", value)
            conn.endheaders()
            self.assertEqual(conn.getresponse().status, 400, values)
            conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", h.port, timeout=5)
        conn.putrequest("POST", "/api/auto-assign")
        conn.putheader("Origin", "http://127.0.0.1:%d" % h.port)
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 411)
        conn.close()

    def test_exact_json_limit_is_read(self):
        h = self.harness()
        body = b'{"zz":"' + b"x" * (1024 * 1024 - 10) + b'"}'
        self.assertEqual(len(body), 1024 * 1024 - 1)
        self.assertEqual(h.raw("POST", "/api/auto-assign", body, {"Content-Type": "application/json"})[0], 200)


class RarityBoundaryTest(unittest.TestCase):
    def test_all_equal_and_single(self):
        self.assertEqual(card_builder.rarity_map({"a": 3, "b": 3}, ["a", "b"]), {"a": 4, "b": 4})
        self.assertEqual(card_builder.rarity_map({"a": 3}, ["a"]), {"a": 4})
        self.assertEqual(card_builder.rarity_map({}, ["a"]), {"a": 1})
        self.assertEqual(card_builder.rarity_map({}, []), {})

    def test_cost_boundary(self):
        self.assertEqual(card_builder.cost_of(20001), 10)
        self.assertEqual(card_builder.cost_of(18001), 10)


if __name__ == "__main__":
    unittest.main()
