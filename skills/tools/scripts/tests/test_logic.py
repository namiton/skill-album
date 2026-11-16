import json
import os
import tempfile
import unittest
from unittest import mock

import helpers

import album_store
import art_store
import atomic
import cards
import scan
import text_store
import usage
from frontmatter import parse_frontmatter

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPG = b"\xff\xd8\xff" + b"0" * 32
WEBP = b"RIFF0000WEBP" + b"0" * 32


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name


class FrontmatterTest(unittest.TestCase):
    def test_plain_and_quotes(self):
        data = parse_frontmatter('---\nname: x\ndescription: "a: b"\nother: \'c\'\n---\nbody')
        self.assertEqual(data["data"], {"name": "x", "description": "a: b", "other": "c"})
        self.assertEqual(data["body"], "body")

    def test_folded_and_literal(self):
        data = parse_frontmatter("---\ndescription: >\n  one\n  two\nmore: |\n  a\n  b\n---\n")["data"]
        self.assertEqual(data["description"], "one two")
        self.assertEqual(data["more"], "a\nb")

    def test_list(self):
        data = parse_frontmatter("---\nallowed-tools:\n  - Read\n  - Bash\ninline: [a, b]\n---\n")["data"]
        self.assertEqual(data["allowed-tools"], ["Read", "Bash"])
        self.assertEqual(data["inline"], ["a", "b"])

    def test_errors_do_not_raise(self):
        self.assertEqual(parse_frontmatter("# x")["error"], "no-frontmatter")
        self.assertEqual(parse_frontmatter("---\nname: x\n")["error"], "unterminated")
        self.assertEqual(parse_frontmatter("---\n: bad\n---\n")["error"], "unparsable")


class AtomicTest(TempCase):
    def test_failure_keeps_original_and_no_temp(self):
        path = os.path.join(self.root, "a.json")
        atomic.save_atomic(path, "one")
        with mock.patch("atomic.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                atomic.save_atomic(path, "two")
        with open(path) as fh:
            self.assertEqual(fh.read(), "one")
        self.assertEqual(os.listdir(self.root), ["a.json"])

    def test_retries_permission_error(self):
        path = os.path.join(self.root, "a.json")
        real = os.replace
        calls = []

        def flaky(a, b):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError()
            real(a, b)

        with mock.patch("atomic.os.replace", side_effect=flaky):
            atomic.save_atomic(path, "ok")
        self.assertEqual(len(calls), 3)


class ScanTest(TempCase):
    def run_scan(self):
        home = helpers.build_home(self.root)
        return home, scan.scan(home, os.path.join(self.root, ".claude.json"))

    def test_exactly_one_card_each(self):
        _, result = self.run_scan()
        ids = [c["id"] for c in result["cards"]]
        self.assertEqual(sorted(ids), sorted(["alpha", "beta-name", "anthropic-skills:pdf", "git-ops:commit", "wiki:wiki"]))
        self.assertEqual(len(ids), len(set(ids)))

    def test_current_org_only_and_fallback(self):
        home, _ = self.run_scan()
        fallback = scan.scan(home, os.path.join(self.root, "missing.json"))
        self.assertIn("anthropic-skills:stale", [c["id"] for c in fallback["cards"]])

    def test_invalid_name_falls_back(self):
        home = helpers.build_home(self.root)
        helpers.write(os.path.join(home, "skills", "gamma", "SKILL.md"), helpers.skill("../../evil"))
        ids = [c["id"] for c in scan.scan(home, os.path.join(self.root, ".claude.json"))["cards"]]
        self.assertIn("gamma", ids)
        self.assertFalse([i for i in ids if ".." in i])

    def test_invalid_folder_is_diagnosed(self):
        home = helpers.build_home(self.root)
        helpers.write(os.path.join(home, "skills", "bad name", "SKILL.md"), helpers.skill())
        result = scan.scan(home, os.path.join(self.root, ".claude.json"))
        self.assertIn("invalid-name", [d["kind"] for d in result["diagnostics"]])

    def test_unresolved_plugin_is_diagnosed(self):
        home = helpers.build_home(self.root)
        path = os.path.join(home, "settings.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["enabledPlugins"]["ghost@x"] = True
        helpers.write(path, json.dumps(data))
        result = scan.scan(home, os.path.join(self.root, ".claude.json"))
        self.assertIn("plugin-unresolved", [d["kind"] for d in result["diagnostics"]])

    def test_source_error_is_isolated(self):
        home = helpers.build_home(self.root)
        with mock.patch("scan._choose_synced", side_effect=RuntimeError("x")):
            result = scan.scan(home, os.path.join(self.root, ".claude.json"))
        self.assertIn("alpha", [c["id"] for c in result["cards"]])
        self.assertIn("source-error", [d["kind"] for d in result["diagnostics"]])

    def test_broken_frontmatter_still_makes_card(self):
        home = helpers.build_home(self.root)
        helpers.write(os.path.join(home, "skills", "broken", "SKILL.md"), "本文だけ\n")
        card = [c for c in scan.scan(home, os.path.join(self.root, ".claude.json"))["cards"] if c["id"] == "broken"][0]
        self.assertTrue(card["parseError"])
        self.assertEqual(card["bodyHead"], "本文だけ")

    def test_scan_writes_nothing_and_reads_no_secret(self):
        home, result = self.run_scan()
        self.assertNotIn("must-not-appear", json.dumps(result, ensure_ascii=False))


def scanned(ids, source="user"):
    return {"cards": [{"id": i, "source": source} for i in ids], "counts": {source: len(ids)}, "diagnostics": []}


class AlbumTest(TempCase):
    def store(self):
        return album_store.AlbumStore(self.root, today=lambda: "2026-11-04")

    def test_initial_numbers_and_folder(self):
        store = self.store()
        store.sync({"cards": [{"id": "git-ops:commit", "source": "plugins"}, {"id": "anthropic-skills:pdf", "source": "synced"}, {"id": "a", "source": "user"}], "counts": {"user": 1}, "diagnostics": []})
        cards_ = store.data["cards"]
        self.assertEqual(cards_["git-ops:commit"]["folder"], "uncategorized")
        self.assertEqual(cards_["anthropic-skills:pdf"]["folder"], "uncategorized")
        self.assertEqual(cards_["a"]["folder"], "uncategorized")
        self.assertIsNone(cards_["a"]["firstSeen"])

    def test_new_card_is_dated_and_numbers_never_reused(self):
        store = self.store()
        store.sync(scanned(["a"]))
        store.sync(scanned(["a", "b"]))
        self.assertEqual(store.data["cards"]["b"]["firstSeen"], "2026-11-04")
        self.assertEqual(store.data["cards"]["b"]["no"], 2)
        store.sync(scanned(["c"]))
        self.assertEqual(store.data["cards"]["c"]["no"], 3)
        self.assertIn("a", store.data["cards"])

    def test_only_cards_added_later_go_into_the_pack(self):
        store = self.store()
        store.sync(scanned(["a"]))
        store.sync(scanned(["a", "b"]))
        self.assertNotIn("packed", store.data["cards"]["a"])
        self.assertTrue(store.data["cards"]["b"]["packed"])

    def test_open_pack_clears_only_named_cards_and_keeps_revision(self):
        store = self.store()
        store.sync(scanned(["a"]))
        store.sync(scanned(["a", "b", "c"]))
        revision = store.data["revision"]
        self.assertEqual(store.open_pack(["b", "unknown", "a"]), {"opened": 1})
        self.assertNotIn("packed", store.data["cards"]["b"])
        self.assertTrue(store.data["cards"]["c"]["packed"])
        self.assertEqual(store.data["revision"], revision)
        self.assertNotIn("packed", self.store().data["cards"]["b"])

    def test_open_pack_rejects_bad_input(self):
        store = self.store()
        for bad in ("a", {"a": 1}, [1], None):
            with self.assertRaises(album_store.StoreError):
                store.open_pack(bad)

    def test_lost_source_reported(self):
        store = self.store()
        store.sync(scanned(["a"]))
        self.assertEqual(store.sync({"cards": [], "counts": {"user": 0}, "diagnostics": []}), ["user"])

    def test_manual_survives_auto_assign(self):
        store = self.store()
        store.sync(scanned(["a", "b"]))
        store.update({"revision": store.data["revision"], "cards": {"a": {"folder": "git"}}})
        self.assertEqual(store.data["cards"]["a"]["by"], "manual")
        result = store.auto_assign({"a": "dev", "b": "dev", "zzz": "dev"})
        self.assertEqual(store.data["cards"]["a"]["folder"], "git")
        self.assertEqual(store.data["cards"]["b"]["folder"], "dev")
        self.assertEqual(result["applied"], 1)
        self.assertEqual(sorted(r["reason"] for r in result["rejected"]), ["manual", "unknown-card"])

    def test_manual_uncategorized_is_kept(self):
        store = self.store()
        store.sync(scanned(["a"]))
        store.update({"revision": store.data["revision"], "cards": {"a": {"folder": "git"}}})
        store.update({"revision": store.data["revision"], "cards": {"a": {"folder": "uncategorized"}}})
        store.auto_assign({"a": "dev"})
        self.assertEqual(store.data["cards"]["a"]["folder"], "uncategorized")

    def test_same_folder_does_not_become_manual(self):
        store = self.store()
        store.sync(scanned(["a"]))
        store.update({"revision": store.data["revision"], "cards": {"a": {"folder": "uncategorized"}}})
        self.assertEqual(store.data["cards"]["a"]["by"], "auto")

    def assertReason(self, store, payload, reason, status=400):
        with self.assertRaises(album_store.StoreError) as ctx:
            store.update(payload)
        self.assertEqual((ctx.exception.reason, ctx.exception.status), (reason, status))

    def test_rejections(self):
        store = self.store()
        store.sync(scanned(["a"]))
        rev = store.data["revision"]
        self.assertReason(store, {"revision": rev - 1}, "revision", 409)
        self.assertReason(store, {"revision": rev, "nextNo": 1}, "schema")
        self.assertReason(store, {"revision": rev, "cards": {"zzz": {"folder": "git"}}}, "unknown-card")
        self.assertReason(store, {"revision": rev, "cards": {"a": {"folder": "none"}}}, "unknown-folder")
        self.assertReason(store, {"revision": rev, "cards": {"a": {"other": 5}}}, "schema")
        self.assertReason(store, {"revision": rev, "cards": {"a": {"artist": "x"}}}, "schema")
        base = store.data["folders"]
        bad = json.loads(json.dumps(base))
        bad[0]["color"] = "red;"
        self.assertReason(store, {"revision": rev, "folders": bad}, "folder-color")
        self.assertReason(store, {"revision": rev, "folders": [f for f in base if f["id"] != "uncategorized"]}, "folder-id")
        renamed = json.loads(json.dumps(base))
        renamed[-1]["name"] = "別名"
        self.assertReason(store, {"revision": rev, "folders": renamed}, "folder-name")
        many = base + [{"id": "x%d" % i, "name": "x", "color": "#000000", "order": 0} for i in range(album_store.MAX_FOLDERS + 1 - len(base))]
        self.assertReason(store, {"revision": rev, "folders": many}, "folders-limit")

    def test_delete_folder_moves_all_cards(self):
        store = self.store()
        store.sync(scanned(["a"]))
        store.update({"revision": store.data["revision"], "cards": {"a": {"folder": "git"}}})
        keep = [f for f in store.data["folders"] if f["id"] != "git"]
        store.update({"revision": store.data["revision"], "folders": keep})
        self.assertEqual(store.data["cards"]["a"]["folder"], "uncategorized")
        self.assertEqual(store.data["cards"]["a"]["by"], "auto")

    def test_restore_from_bak_and_reset(self):
        store = self.store()
        store.sync(scanned(["a"]))
        with open(store.path, "w") as fh:
            fh.write("{broken")
        again = self.store()
        self.assertEqual(again.notice, "restored")
        self.assertIn("a", again.data["cards"])
        with open(store.bak, "w") as fh:
            fh.write("{broken")
        third = self.store()
        self.assertEqual(third.notice, "reset")
        self.assertTrue([n for n in os.listdir(self.root) if n.startswith("album.broken-")])

    def test_interrupted_write_keeps_data(self):
        store = self.store()
        store.sync(scanned(["a"]))
        with mock.patch("atomic.os.replace", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                store.update({"revision": store.data["revision"], "sound": False})
        self.assertIn("a", self.store().data["cards"])

    def test_newer_version_is_readonly(self):
        helpers.write(os.path.join(self.root, "album.json"), json.dumps({"version": 99}))
        store = self.store()
        self.assertTrue(store.readonly)
        self.assertReason(store, {"revision": 0}, "readonly", 409)
        with open(store.path) as fh:
            self.assertEqual(json.load(fh), {"version": 99})


class CardsTest(unittest.TestCase):
    def test_cost(self):
        self.assertEqual([cards.cost_of(n) for n in (0, 1, 2000, 2001, 99999)], [1, 1, 1, 2, 10])

    def test_rarity_bands_and_ties(self):
        ids = ["c%d" % i for i in range(10)]
        counts = {"c0": 100, "c1": 50, "c2": 50, "c3": 10, "c4": 9, "c5": 8, "c6": 7}
        rarity = cards.rarity_map(counts, ids)
        self.assertEqual(rarity["c0"], 4)
        self.assertEqual((rarity["c1"], rarity["c2"]), (3, 3))
        self.assertEqual(rarity["c3"], 2)
        self.assertEqual(rarity["c9"], 1)

    def test_rarity_unknown_is_one(self):
        self.assertEqual(cards.rarity_map({"a": 9}, ["a"], known=False)["a"], 1)

    def test_sentence_and_triggers(self):
        self.assertEqual(cards.first_sentence("一文目。二文目。"), "一文目。")
        self.assertEqual(cards.first_sentence("First one. Second."), "First one.")
        self.assertEqual(cards.triggers_of("説明「無視」。トリガー「バグ」「直らない」"), ["バグ", "直らない"])
        self.assertEqual(cards.triggers_of("説明のみ"), [])

    def test_is_japanese(self):
        for text in ("要約", "GitHub Actions の workflow run 履歴・ログ閲覧。", "GitHub でissueを作成する。"):
            self.assertTrue(cards.is_japanese(text), text)
        for text in ("", "First one. Second.", "Use this skill for docs — 文書 and every kind of long written deliverable."):
            self.assertFalse(cards.is_japanese(text), text)


def tool_line(tool_id, skill, stamp="2026-11-01T00:00:00Z", args="秘密の本文"):
    return json.dumps({"timestamp": stamp, "type": "assistant", "message": {"content": [
        {"type": "text", "text": '会話の本文 "name":"Skill"'},
        {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": skill, "args": args}},
    ]}}, ensure_ascii=False)


class UsageTest(TempCase):
    def setUp(self):
        super().setUp()
        self.projects = os.path.join(self.root, "projects")
        self.data = os.path.join(self.root, "data")

    def tracker(self):
        return usage.UsageTracker(self.projects, self.data)

    def test_ignores_text_mentions_and_args(self):
        helpers.write(os.path.join(self.projects, "p", "a.jsonl"), tool_line("t1", "git-ops:commit") + "\n" + json.dumps({"message": {"content": [{"type": "text", "text": '"name":"Skill","input":{"skill":"fake"}'}]}}) + "\n")
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.summary()["counts"], {"git-ops:commit": 1})
        with open(tracker.ledger, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]
        self.assertEqual(set(rows[0]), {"id", "skill", "ts"})
        with open(tracker.ledger, encoding="utf-8") as fh:
            self.assertNotIn("秘密", fh.read())

    def test_dedupes_across_files_and_subagents(self):
        line = tool_line("same", "a")
        helpers.write(os.path.join(self.projects, "p", "a.jsonl"), line + "\n" + line + "\n")
        helpers.write(os.path.join(self.projects, "p", "b.jsonl"), line + "\n")
        helpers.write(os.path.join(self.projects, "p", "s", "subagents", "x.jsonl"), line + "\n" + tool_line("other", "a") + "\n")
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.summary()["counts"], {"a": 2})

    def test_rescan_is_stable_and_survives_deleted_history(self):
        path = os.path.join(self.projects, "p", "a.jsonl")
        helpers.write(path, tool_line("t1", "a") + "\n")
        self.tracker().scan()
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.summary()["counts"], {"a": 1})
        os.remove(path)
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.summary()["counts"], {"a": 1})

    def test_changed_file_is_reread(self):
        path = os.path.join(self.projects, "p", "a.jsonl")
        helpers.write(path, tool_line("t1", "a") + "\n")
        self.tracker().scan()
        helpers.write(path, tool_line("t1", "a") + "\n" + tool_line("t2", "a") + "\n")
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.summary()["counts"], {"a": 2})

    def test_broken_tail_is_trimmed(self):
        helpers.write(os.path.join(self.data, "usage-events.jsonl"), '{"id":"x","skill":"a","ts":""}\n{"id":"y","sk')
        tracker = self.tracker()
        self.assertEqual(list(tracker.events), ["x"])
        with open(tracker.ledger, "rb") as fh:
            self.assertTrue(fh.read().endswith(b"\n"))

    def test_unknown_format_is_unreadable(self):
        helpers.write(os.path.join(self.projects, "p", "a.jsonl"), "\n".join(json.dumps({"event": i}) for i in range(25)) + "\n")
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.state, "unreadable")

    def test_short_new_file_is_not_unreadable(self):
        helpers.write(os.path.join(self.projects, "p", "a.jsonl"), json.dumps({"event": 1}) + "\n")
        tracker = self.tracker()
        tracker.scan()
        self.assertEqual(tracker.state, "ok")

    def test_typed_events(self):
        seq = 1793796194000
        helpers.write(os.path.join(self.data, "usage-inbox", helpers.SESSION + ".json"), json.dumps({"seq": seq, "skill": "a"}))
        helpers.write(os.path.join(self.data, "usage-inbox", "bad.json"), json.dumps({"seq": seq, "skill": "b"}))
        tracker = self.tracker()
        tracker.ingest_typed({"a", "b"})
        tracker.ingest_typed({"a", "b"})
        self.assertEqual(tracker.summary()["counts"], {"a": 1})

    def test_typed_unknown_word_is_not_stored(self):
        helpers.write(os.path.join(self.data, "usage-inbox", helpers.SESSION + ".json"), json.dumps({"seq": 1793796194000, "skill": "secret-word"}))
        tracker = self.tracker()
        tracker.ingest_typed({"a"})
        self.assertEqual(tracker.events, {})
        self.assertFalse(os.path.exists(tracker.ledger))

    def test_typed_rejects_bad_values(self):
        helpers.write(os.path.join(self.data, "usage-inbox", helpers.SESSION + ".json"), json.dumps({"seq": 5, "skill": "a\nb"}))
        tracker = self.tracker()
        tracker.ingest_typed({"a"})
        self.assertEqual(tracker.summary()["counts"], {})

    def test_typed_and_tool_not_double_counted(self):
        tracker = self.tracker()
        tracker.events = {
            "1": {"id": "1", "skill": "a", "ts": "2026-11-01T00:00:10Z"},
            "2": {"id": "2", "skill": "a", "ts": "2026-11-01T00:00:00Z", "typed": True},
            "3": {"id": "3", "skill": "a", "ts": "2026-11-01T02:00:00Z", "typed": True},
        }
        self.assertEqual(tracker.summary()["counts"], {"a": 2})


class ArtTest(TempCase):
    def store(self):
        return art_store.ArtStore(self.root)

    def test_formats(self):
        for data, ext in ((PNG, "png"), (JPG, "jpg"), (WEBP, "webp")):
            self.assertEqual(art_store.sniff(data), ext)
        for bad in (b"<svg xmlns='x'><script/></svg>", b"RIFF0000WAVEfmt ", b"GIF89a", b""):
            self.assertIsNone(art_store.sniff(bad))

    def test_colon_is_a_real_file(self):
        store = self.store()
        store.put("git-ops:commit", PNG)
        self.assertEqual(os.listdir(store.art), ["git-ops%3Acommit.png"])
        self.assertEqual(store.read("git-ops:commit"), (PNG, "image/png"))

    def test_rejected_keeps_old(self):
        store = self.store()
        store.put("a", PNG)
        with self.assertRaises(art_store.ArtError):
            store.put("a", b"<svg/>")
        with self.assertRaises(art_store.ArtError) as ctx:
            store.put("a", PNG + b"0" * art_store.MAX_BYTES)
        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(store.read("a")[0], PNG)
        self.assertFalse(store.state("a")["hasPrev"])

    def test_if_absent(self):
        store = self.store()
        store.put("a", PNG, if_absent=True)
        with self.assertRaises(art_store.ArtError) as ctx:
            store.put("a", JPG, if_absent=True)
        self.assertEqual((ctx.exception.reason, ctx.exception.status), ("exists", 409))
        self.assertEqual(store.read("a")[0], PNG)

    def test_replace_delete_restore(self):
        store = self.store()
        store.put("a", PNG)
        store.put("a", JPG)
        self.assertEqual(store.read("a")[0], JPG)
        store.restore("a")
        self.assertEqual(store.read("a")[0], PNG)
        store.restore("a")
        self.assertEqual(store.read("a")[0], JPG)
        store.delete("a")
        self.assertIsNone(store.read("a"))
        store.restore("a")
        self.assertEqual(store.read("a")[0], JPG)

    def test_failed_write_keeps_old(self):
        store = self.store()
        store.put("a", PNG)
        with mock.patch("art_store.save_atomic", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                store.put("a", JPG)
        self.assertEqual(store.read("a")[0], PNG)

    def test_traversal_stays_inside(self):
        store = self.store()
        store.put("../../x", PNG)
        self.assertEqual(os.listdir(self.root), ["art"])


class TextTest(TempCase):
    CARD = {"a": {"id": "a", "sourceHash": "h", "description": "これは二十文字を超える説明文の原文であり転載してはならない内容です"}}

    def store(self):
        return text_store.TextStore(self.root)

    def good(self):
        return {"summary": "要約", "flavor": "一言", "sourceHash": "h"}

    def test_texts_validation(self):
        store = self.store()
        cases = {
            "too-long": {"summary": "あ" * 81},
            "newline": {"flavor": "a\nb"},
            "url": {"summary": "https://x.example"},
            "path": {"flavor": r"C:\Users\x"},
            "mail": {"summary": "a@b.example"},
            "stale": {"sourceHash": "old"},
            "command": {"summary": "git push する"},
        }
        for reason, change in cases.items():
            item = dict(self.good(), **change)
            self.assertEqual(store.apply_texts({"a": item}, self.CARD)["rejected"], [{"id": "a", "reason": reason}], reason)
        self.assertEqual(store.apply_texts({"z": self.good()}, self.CARD)["rejected"][0]["reason"], "unknown-card")
        self.assertEqual(store.apply_texts({"a": self.good()}, self.CARD)["applied"], 1)

    def test_texts_reject_non_japanese(self):
        store = self.store()
        for change in ({"summary": "Creates a commit."}, {"flavor": "One line."}):
            item = dict(self.good(), **change)
            self.assertEqual(store.apply_texts({"a": item}, self.CARD)["rejected"], [{"id": "a", "reason": "not-japanese"}])
        self.assertEqual(store.texts, {})

    def test_rejected_does_not_echo_input(self):
        result = self.store().apply_texts({"a": dict(self.good(), summary="https://evil.example")}, self.CARD)
        self.assertNotIn("evil", json.dumps(result))

    def test_motif_validation(self):
        store = self.store()
        for reason, motif in (("too-long", "あ" * 121), ("token", "abcdEFGH12345678xx"), ("url", "http://x"), ("path", "~/x"), ("copied", "二十文字を超える説明文の原文であり転載してはならない")):
            self.assertEqual(store.apply_motifs({"a": motif}, self.CARD)["rejected"], [{"id": "a", "reason": reason}], reason)
        self.assertEqual(store.apply_motifs({"a": "光る虫眼鏡"}, self.CARD)["applied"], 1)

    def test_prompts_only_without_art(self):
        store = self.store()
        store.motifs = {"a:b": "光る虫眼鏡", "c": "金の鍵"}
        store.write_prompts([{"id": "a:b", "name": "b", "no": 17, "hasArt": False}, {"id": "c", "name": "c", "no": 2, "hasArt": True}, {"id": "d", "name": "d", "no": 3, "hasArt": False}])
        with open(store.prompts_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("017_a_b.png", text)
        self.assertIn("光る虫眼鏡", text)
        self.assertNotIn("金の鍵", text)
        self.assertNotIn("\\", text)

    def test_card_prompt_without_motif(self):
        self.assertEqual(self.store().card_prompt({"id": "x", "name": "x", "no": 1}), {"state": "no-motif"})

    def test_broken_file_is_moved(self):
        helpers.write(os.path.join(self.root, "card-texts.json"), "{broken")
        self.assertEqual(self.store().texts, {})
        self.assertTrue(os.path.exists(os.path.join(self.root, "card-texts.json.broken")))


if __name__ == "__main__":
    unittest.main()
