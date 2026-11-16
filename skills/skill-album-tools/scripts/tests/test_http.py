import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import helpers
from test_logic import JPG, PNG

import apply as apply_cli
import launcher
import server as server_module


def tree_hash(root):
    digest = hashlib.sha256()
    for base, dirs, files in sorted(os.walk(root)):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            digest.update(os.path.relpath(path, root).encode())
            with open(path, "rb") as fh:
                digest.update(fh.read())
    return digest.hexdigest()


class ServerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.h = helpers.Harness(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.h.close)
        self.origin = "http://127.0.0.1:%d" % self.h.port


class SecurityTest(ServerCase):
    def test_bad_host(self):
        for host in ("evil.example", "localhost:%d" % self.h.port, "127.0.0.1"):
            self.assertEqual(self.h.raw("GET", "/api/ping", headers={"Host": host})[0], 403, host)

    def test_origin_required(self):
        for origin in ("https://evil.example", "null", "http://localhost:%d" % self.h.port):
            status = self.h.raw("POST", "/api/shutdown", headers={"Origin": origin, "Content-Length": "0"})[0]
            self.assertEqual(status, 403, origin)
        conn_status = self.h.raw("DELETE", "/api/cards/alpha/art", headers={"Origin": ""})[0]
        self.assertEqual(conn_status, 403)

    def test_content_type_and_length(self):
        rev = self.h.json("GET", "/api/album")[1]["revision"]
        body = json.dumps({"revision": rev}).encode()
        self.assertEqual(self.h.raw("PUT", "/api/album", body, {"Content-Type": "text/plain"})[0], 415)
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
        conn.putrequest("PUT", "/api/album")
        conn.putheader("Origin", self.origin)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(1024 * 1024 + 1))
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()
        self.assertEqual(self.h.raw("POST", "/api/auto-assign", headers={"Content-Length": "-1"})[0], 400)
        self.assertEqual(self.h.raw("PUT", "/api/cards/alpha/art", b"{}", {"Content-Type": "application/json"})[0], 415)

    def test_delete_without_length_is_allowed(self):
        self.assertEqual(self.h.raw("DELETE", "/api/cards/alpha/art")[0], 200)

    def test_headers_and_no_cors(self):
        status, _, headers = self.h.raw("GET", "/api/ping")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_mime_table(self):
        self.assertTrue(self.h.raw("GET", "/a.js")[2]["Content-Type"].startswith("text/javascript"))
        self.assertTrue(self.h.raw("GET", "/")[2]["Content-Type"].startswith("text/html"))
        self.assertEqual(self.h.raw("GET", "/secret.txt")[0], 404)

    def test_traversal(self):
        for path in ("/../secret.md", "/..%2f..%2f.claude%2f.env%2fsecret.md", "/%2e%2e/%2e%2e/.claude/.env/secret.md", "/..\\x.js", "/api/cards/..%2f..", "/api/cards/.env", "/api/cards/..%2f.env/art"):
            status, body, _ = self.h.raw("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"must-not-appear", body)

    def test_no_secret_in_responses(self):
        for path in ("/api/cards", "/api/album", "/api/usage", "/api/state", "/api/export?kind=classify", "/api/export?kind=texts", "/api/export?kind=prompts"):
            self.assertNotIn(b"must-not-appear", self.h.raw("GET", path)[1], path)

    def test_open_ignores_path(self):
        status = self.h.json("POST", "/api/cards/alpha/open", {"path": "C:\\Windows"})[0]
        self.assertEqual(status, 200)
        self.assertEqual(self.h.opened, [os.path.join(self.h.home, "skills", "alpha")])
        self.assertEqual(self.h.json("POST", "/api/cards/nope/open")[0], 404)
        self.assertEqual(len(self.h.opened), 1)

    def test_get_does_not_change_state(self):
        self.assertEqual(self.h.raw("GET", "/api/cards/alpha/open")[0], 404)
        self.assertEqual(self.h.raw("GET", "/api/shutdown")[0], 404)

    def test_script_in_skill_is_returned_as_json_text(self):
        helpers.write(os.path.join(self.h.home, "skills", "evil", "SKILL.md"), helpers.skill("evil", "<img src=x onerror=alert(1)>"))
        self.h.app.refresh(force=True)
        status, body, headers = self.h.raw("GET", "/api/cards/evil")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))

    def test_error_does_not_leak(self):
        def boom(_):
            raise RuntimeError("C:\\secret\\path")

        self.h.app.opener = boom
        status, body, _ = self.h.raw("POST", "/api/cards/alpha/open", headers={"Content-Length": "0"})
        self.assertEqual(status, 500)
        self.assertNotIn(b"secret", body)


class SkillFilesUntouchedTest(ServerCase):
    def test_operations_keep_skills_intact(self):
        skills = os.path.join(self.h.home, "skills")
        before = tree_hash(skills)
        rev = self.h.json("GET", "/api/album")[1]["revision"]
        self.h.cards()
        self.h.json("PUT", "/api/album", {"revision": rev, "cards": {"alpha": {"folder": "git"}}})
        self.h.raw("PUT", "/api/cards/alpha/art", PNG, {"Content-Type": "image/png"})
        self.h.json("POST", "/api/auto-assign", {"beta-name": "dev"})
        self.h.json("POST", "/api/motifs", {"alpha": "光る虫眼鏡"})
        self.assertEqual(tree_hash(skills), before)


class CardsApiTest(ServerCase):
    def test_list(self):
        data = self.h.json("GET", "/api/cards")[1]
        self.assertEqual(len(data["cards"]), 5)
        self.assertEqual(data["maxNo"], 5)
        beta = {c["id"]: c for c in data["cards"]}["beta-name"]
        self.assertEqual(beta["kind"], "自作")
        self.assertEqual(beta["triggers"], ["ベータ", "試す"])
        self.assertEqual(beta["tools"], ["Read", "Bash"])
        self.assertFalse(beta["hasArt"])
        self.assertNotIn("path", beta)

    def test_pack_lists_new_cards_until_opened(self):
        self.assertFalse(self.h.cards()["alpha"]["packed"])
        helpers.write(os.path.join(self.h.home, "skills", "added", "SKILL.md"), helpers.skill(name="added"))
        self.h.app.refresh(force=True)
        self.assertTrue(self.h.cards()["added"]["packed"])
        self.assertEqual(self.h.json("POST", "/api/pack/open", {"ids": ["added"]}), (200, {"opened": 1}))
        self.assertFalse(self.h.cards()["added"]["packed"])

    def test_pack_open_validates_body(self):
        self.assertEqual(self.h.json("POST", "/api/pack/open", {"ids": "added"})[0], 400)
        self.assertEqual(self.h.json("POST", "/api/pack/open", {})[0], 400)

    def test_detail(self):
        detail = self.h.json("GET", "/api/cards/alpha")[1]
        self.assertIn("手順の本文", detail["body"])
        self.assertIn("scripts/a.py", detail["files"])

    def test_plugin_id_with_colon(self):
        self.assertEqual(self.h.json("GET", "/api/cards/git-ops%3Acommit")[0], 200)

    def test_revision_conflict(self):
        rev = self.h.json("GET", "/api/cards")[1]["revision"]
        self.assertEqual(self.h.json("PUT", "/api/album", {"revision": rev, "sound": False})[0], 200)
        status, body = self.h.json("PUT", "/api/album", {"revision": rev, "sound": True})
        self.assertEqual((status, body["reason"]), (409, "revision"))

    def test_partial_merge_keeps_removed_skill(self):
        self.h.cards()
        rev = self.h.json("GET", "/api/album")[1]["revision"]
        self.h.json("PUT", "/api/album", {"revision": rev, "cards": {"alpha": {"folder": "git"}}})
        rev = self.h.json("GET", "/api/album")[1]["revision"]
        self.h.json("PUT", "/api/album", {"revision": rev, "cards": {"beta-name": {"folder": "dev"}}})
        self.assertEqual(self.h.json("GET", "/api/album")[1]["cards"]["alpha"]["folder"], "git")

    def test_usage_counts_and_unknown(self):
        self.h.app.usage.events = {"1": {"id": "1", "skill": "alpha", "ts": "2026-11-01T00:00:00Z"}}
        cards = self.h.cards()
        self.assertEqual(cards["alpha"]["count"], 1)
        self.assertEqual(cards["alpha"]["rarity"], 4)
        self.assertEqual(cards["beta-name"]["count"], 0)
        self.h.app.usage.state = "unreadable"
        cards = self.h.cards()
        self.assertIsNone(cards["alpha"]["count"])
        self.assertEqual(cards["alpha"]["rarity"], 1)

    def test_summary_uses_fresh_text_only(self):
        hash_ = self.h.app.card_index()["alpha"]["sourceHash"]
        self.h.json("POST", "/api/texts", {"alpha": {"summary": "要約", "flavor": "一言", "sourceHash": hash_}})
        card = self.h.cards()["alpha"]
        self.assertEqual((card["summary"], card["flavor"]), ("要約", "一言"))
        helpers.write(os.path.join(self.h.home, "skills", "alpha", "SKILL.md"), helpers.skill(description="変更後の説明。次。"))
        self.h.app.refresh(force=True)
        card = self.h.cards()["alpha"]
        self.assertEqual(card["summary"], "変更後の説明。")
        self.assertIsNone(card["flavor"])

    def test_summary_never_shows_english_source(self):
        path = os.path.join(self.h.home, "skills", "alpha", "SKILL.md")
        helpers.write(path, helpers.skill(description="Creates a commit. Then pushes."))
        self.h.app.refresh(force=True)
        self.assertEqual(self.h.cards()["alpha"]["summary"], "")
        hash_ = self.h.app.card_index()["alpha"]["sourceHash"]
        self.h.json("POST", "/api/texts", {"alpha": {"summary": "要約", "flavor": "一言", "sourceHash": hash_}})
        helpers.write(path, helpers.skill(description="Creates a commit and more. Then pushes."))
        self.h.app.refresh(force=True)
        card = self.h.cards()["alpha"]
        self.assertEqual(card["summary"], "要約")
        self.assertIsNone(card["flavor"])

    def test_state_changes_with_revision(self):
        before = self.h.json("GET", "/api/state")[1]
        self.h.json("POST", "/api/auto-assign", {"alpha": "dev"})
        after = self.h.json("GET", "/api/state")[1]
        self.assertGreater(after["albumRevision"], before["albumRevision"])
        self.assertEqual(after["version"], server_module.VERSION)

    def test_readonly_rejects_all_writes(self):
        self.h.app.album.readonly = True
        self.assertEqual(self.h.json("PUT", "/api/album", {"revision": 0})[1]["reason"], "readonly")
        self.assertEqual(self.h.raw("PUT", "/api/cards/alpha/art", PNG, {"Content-Type": "image/png"})[0], 409)
        self.assertEqual(self.h.json("DELETE", "/api/cards/alpha/art")[1]["reason"], "readonly")
        self.assertEqual(self.h.json("POST", "/api/auto-assign", {})[1]["reason"], "readonly")
        self.assertEqual(self.h.json("POST", "/api/texts", {})[1]["reason"], "readonly")


class UseTest(ServerCase):
    def use(self, card="alpha", session=helpers.SESSION):
        return self.h.json("POST", "/api/cards/%s/use?s=%s" % (card, session))

    def test_writes_only_validated_request(self):
        status, body = self.use("git-ops%3Acommit")
        self.assertEqual(status, 200)
        with open(os.path.join(self.h.data, "inbox", helpers.SESSION + ".json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"seq": body["seq"], "id": "git-ops:commit"})

    def test_rejects_bad_values(self):
        for session in ("", "..%2f..%2fx", "x" * 36, helpers.SESSION + "%0A", "../" + helpers.SESSION[3:]):
            self.assertEqual(self.use(session=session)[0], 400, session)
        self.assertEqual(self.use("nope")[0], 404)
        self.assertEqual(os.path.exists(os.path.join(self.h.data, "inbox")), False)

    def test_seq_grows_across_restart(self):
        first = self.use()[1]["seq"]
        second = self.use()[1]["seq"]
        self.assertGreater(second, first)
        self.h.app.last_seq = 0
        self.assertGreaterEqual(self.use()[1]["seq"], first)

    def test_result(self):
        seq = self.use()[1]["seq"]
        path = "/api/use-result?s=%s&seq=%d" % (helpers.SESSION, seq)
        self.assertEqual(self.h.json("GET", path)[1]["state"], "pending")
        outbox = os.path.join(self.h.data, "outbox", helpers.SESSION + ".json")
        helpers.write(outbox, json.dumps({"seq": seq - 1, "isFilled": True}))
        self.assertEqual(self.h.json("GET", path)[1]["state"], "pending")
        helpers.write(outbox, '{"seq":')
        self.assertEqual(self.h.json("GET", path)[1]["state"], "pending")
        helpers.write(outbox, json.dumps({"seq": seq, "isFilled": False}))
        self.assertEqual(self.h.json("GET", path)[1]["state"], "refused")
        helpers.write(outbox, json.dumps({"seq": seq, "isFilled": True}))
        self.assertEqual(self.h.json("GET", path)[1]["state"], "filled")

    def test_result_rejects_bad_seq(self):
        for seq in ("", "../../x", "-1", "0", "99999999999999999", "1.5"):
            path = "/api/use-result?s=%s&seq=%s" % (helpers.SESSION, seq)
            self.assertEqual(self.h.json("GET", path)[0], 400, seq)


class ArtTextsApiTest(ServerCase):
    def put(self, data, kind="image/png", query="", card="alpha"):
        return self.h.raw("PUT", "/api/cards/%s/art%s" % (card, query), data, {"Content-Type": kind})

    def test_put_get_replace_restore(self):
        self.assertEqual(self.put(PNG)[0], 200)
        status, body, headers = self.h.raw("GET", "/api/cards/alpha/art")
        self.assertEqual((status, body, headers["Content-Type"]), (200, PNG, "image/png"))
        self.assertEqual(self.put(JPG, "image/jpeg")[0], 200)
        self.assertTrue(self.h.cards()["alpha"]["hasPrev"])
        self.h.json("POST", "/api/cards/alpha/art/restore")
        self.assertEqual(self.h.raw("GET", "/api/cards/alpha/art")[1], PNG)

    def test_spoofed_type_is_refused_and_old_kept(self):
        self.put(PNG)
        status, body, _ = self.put(b"<svg onload=alert(1)>", "image/png")
        self.assertEqual((status, json.loads(body)["reason"]), (400, "format"))
        self.assertEqual(self.h.raw("GET", "/api/cards/alpha/art")[1], PNG)

    def test_too_large(self):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
        conn.putrequest("PUT", "/api/cards/alpha/art")
        conn.putheader("Origin", self.origin)
        conn.putheader("Content-Type", "image/png")
        conn.putheader("Content-Length", str(10 * 1024 * 1024 + 1))
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()
        self.assertFalse(self.h.cards()["alpha"]["hasArt"])

    def test_if_absent_conflict(self):
        self.assertEqual(self.put(PNG, query="?ifAbsent=1")[0], 200)
        status, body, _ = self.put(JPG, "image/jpeg", "?ifAbsent=1")
        self.assertEqual((status, json.loads(body)["reason"]), (409, "exists"))
        self.assertEqual(self.h.raw("GET", "/api/cards/alpha/art")[1], PNG)

    def test_export_texts_has_no_body(self):
        data = self.h.json("GET", "/api/export?kind=texts")[1]
        row = [c for c in data["cards"] if c["id"] == "alpha"][0]
        self.assertEqual(set(row), {"id", "sourceHash", "path"})
        self.assertNotIn("手順の本文", json.dumps(data, ensure_ascii=False))

    def test_export_classify_only_id_and_description(self):
        data = self.h.json("GET", "/api/export?kind=classify")[1]
        self.assertEqual(set(data["cards"][0]), {"id", "description"})
        self.assertNotIn("手順の本文", json.dumps(data, ensure_ascii=False))

    def test_motifs_write_prompts(self):
        result = self.h.json("POST", "/api/motifs", {"alpha": "光る虫眼鏡", "beta-name": "https://x"})[1]
        self.assertEqual(result["applied"], 1)
        with open(os.path.join(self.h.data, "art-prompts.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("光る虫眼鏡", text)
        self.assertNotIn(self.h.home, text)
        self.assertNotIn("手順の本文", text)
        self.assertEqual(self.h.json("GET", "/api/cards/alpha/prompt")[1]["state"], "ok")
        self.assertEqual(self.h.json("GET", "/api/cards/beta-name/prompt")[1]["state"], "no-motif")


class BindTest(unittest.TestCase):
    def test_second_server_cannot_bind(self):
        with tempfile.TemporaryDirectory() as root:
            first = helpers.Harness(root)
            try:
                app = server_module.App(first.home, first.data, first.web, os.path.join(root, ".claude.json"))
                with self.assertRaises(OSError):
                    server_module.make_server(app, first.port)
            finally:
                first.close()

    def test_rebind_after_requests(self):
        with tempfile.TemporaryDirectory() as root:
            first = helpers.Harness(root)
            port = first.port
            for _ in range(5):
                first.raw("GET", "/api/ping")
            first.close()
            app = server_module.App(first.home, first.data, first.web, os.path.join(root, ".claude.json"))
            second = server_module.make_server(app, port)
            second.server_close()


class LauncherTest(ServerCase):
    def test_ping_states(self):
        self.assertEqual(launcher.ping(self.h.port), "ours")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        free = sock.getsockname()[1]
        sock.close()
        self.assertEqual(launcher.ping(free), "none")

    def test_other_app_is_port_busy(self):
        import http.server
        import threading

        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        other = http.server.HTTPServer(("127.0.0.1", 0), Other)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            self.assertEqual(launcher.ping(other.server_address[1]), "other")
            self.assertEqual(launcher.ensure(other.server_address[1], self.h.data), "port-busy")
        finally:
            other.shutdown()
            other.server_close()

    def test_reuses_running_server(self):
        self.assertEqual(launcher.ensure(self.h.port, self.h.data), "ok")

    def test_bad_session(self):
        for value in ("../x", helpers.SESSION + "\n", "abc"):
            self.assertEqual(launcher.main(["--session", value, "--port", str(self.h.port), "--no-browser"]), 2)


class EndToEndTest(unittest.TestCase):
    def test_launcher_spawns_detached_and_shutdown(self):
        with tempfile.TemporaryDirectory() as root:
            home = helpers.build_home(root)
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()
            data = os.path.join(root, "fresh", "data")
            script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "launcher.py")
            out = subprocess.run([sys.executable, "-I", script, "--no-browser", "--port", str(port), "--data-dir", data, "--claude-home", home], capture_output=True, text=True, timeout=20)
            self.assertEqual(json.loads(out.stdout)["status"], "ok", out.stderr)
            self.assertTrue(os.path.isdir(data))
            try:
                self.assertEqual(launcher.ping(port), "ours")
                self.assertEqual(apply_cli.call(port, "GET", "/api/export?kind=classify")[0], 200)
            finally:
                launcher.request(port, "POST", "/api/shutdown")
            deadline = time.time() + 5
            while time.time() < deadline and launcher.ping(port) != "none":
                time.sleep(0.1)
            self.assertEqual(launcher.ping(port), "none")
            time.sleep(0.5)


class ApplyTest(ServerCase):
    def test_commands_are_fixed(self):
        for command in ("shutdown", "put"):
            with self.assertRaises(SystemExit):
                apply_cli.main([command, "--port", str(self.h.port)])

    def test_classify_from_file(self):
        plan = os.path.join(self._tmp.name, "plan.json")
        helpers.write(plan, json.dumps({"alpha": "dev"}))
        code = apply_cli.main(["classify", plan, "--port", str(self.h.port), "--data-dir", self.h.data])
        self.assertEqual(code, 0)
        self.assertEqual(self.h.json("GET", "/api/album")[1]["cards"]["alpha"]["folder"], "dev")

    def test_proxy_env_is_ignored(self):
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
        self.addCleanup(os.environ.pop, "HTTP_PROXY", None)
        self.assertEqual(apply_cli.call(self.h.port, "GET", "/api/ping")[0], 200)


if __name__ == "__main__":
    unittest.main()
