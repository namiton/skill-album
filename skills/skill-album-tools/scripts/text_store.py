"""カード文章と図柄説明の検証と保存、指示書の出力。"""
import json
import os
import re
import shutil

from atomic import save_atomic, save_json
from cards import is_japanese

SUMMARY_MAX = 80
FLAVOR_MAX = 40
MOTIF_MAX = 120
URL_RE = re.compile(r"https?://|www\.", re.I)
PATH_RE = re.compile(r"[A-Za-z]:[\\/]|~[\\/]|\\\\|(?:^|\s)/[\w.-]+/|\.\.[\\/]|\.env\b")
MAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
TOKEN_RE = re.compile(r"[A-Za-z0-9]{16,}")
COMMAND_RE = re.compile(r"(?:^|\s)(?:/[a-z][\w:-]*\s|git\s|rm\s|curl\s|sudo\s|py\s|python\s)", re.I)

STYLE = """# カード絵の生成指示書

## 共通の画風
- 添付の参考画像と同じ、やわらかいイラストのタッチ
- 横長 3:2
- 画像の中に文字、数字、ロゴを入れない
- 主役を中央に 1 つ置き、背景は淡く
- カードの枠は描かない

## カードごとの図柄
"""


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        pass
    try:
        shutil.copyfile(path, path + ".broken")
    except OSError:
        pass
    return {}


def _safe(text, limit):
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if len(text) > limit:
        return "too-long"
    if "\n" in text or "\r" in text:
        return "newline"
    if URL_RE.search(text):
        return "url"
    if PATH_RE.search(text):
        return "path"
    if MAIL_RE.search(text):
        return "mail"
    return None


def _shares_run(text, source, size=20):
    if not source:
        return False
    return any(text[i:i + size] in source for i in range(0, max(0, len(text) - size + 1)))


def file_stem(no, card_id):
    return "%03d_%s" % (no, card_id.replace(":", "_"))


class TextStore:
    def __init__(self, data_dir):
        self.texts_path = os.path.join(data_dir, "card-texts.json")
        self.motifs_path = os.path.join(data_dir, "art-motifs.json")
        self.prompts_path = os.path.join(data_dir, "art-prompts.md")
        self.texts = _load(self.texts_path)
        self.motifs = _load(self.motifs_path)
        self.revision = 0

    def apply_texts(self, items, cards):
        """cards は id からカードを引く辞書。"""
        applied = 0
        rejected = []
        for card_id, item in (items if isinstance(items, dict) else {}).items():
            short = str(card_id)[:64]
            card = cards.get(card_id)
            if card is None:
                rejected.append({"id": short, "reason": "unknown-card"})
                continue
            if not isinstance(item, dict) or set(item) - {"summary", "flavor", "sourceHash"}:
                rejected.append({"id": card_id, "reason": "schema"})
                continue
            if item.get("sourceHash") != card["sourceHash"]:
                rejected.append({"id": card_id, "reason": "stale"})
                continue
            problem = _safe(item.get("summary"), SUMMARY_MAX) or _safe(item.get("flavor"), FLAVOR_MAX)
            if problem:
                rejected.append({"id": card_id, "reason": problem})
                continue
            if COMMAND_RE.search(item["summary"]) or COMMAND_RE.search(item["flavor"]):
                rejected.append({"id": card_id, "reason": "command"})
                continue
            if not (is_japanese(item["summary"]) and is_japanese(item["flavor"])):
                rejected.append({"id": card_id, "reason": "not-japanese"})
                continue
            self.texts[card_id] = {"summary": item["summary"], "flavor": item["flavor"], "sourceHash": item["sourceHash"]}
            applied += 1
        if applied:
            save_json(self.texts_path, self.texts)
            self.revision += 1
        return {"applied": applied, "rejected": rejected}

    def apply_motifs(self, items, cards):
        applied = 0
        rejected = []
        for card_id, motif in (items if isinstance(items, dict) else {}).items():
            short = str(card_id)[:64]
            card = cards.get(card_id)
            if card is None:
                rejected.append({"id": short, "reason": "unknown-card"})
                continue
            problem = _safe(motif, MOTIF_MAX)
            if not problem and TOKEN_RE.search(motif):
                problem = "token"
            if not problem and _shares_run(motif, card["description"]):
                problem = "copied"
            if problem:
                rejected.append({"id": card_id, "reason": problem})
                continue
            self.motifs[card_id] = motif
            applied += 1
        if applied:
            save_json(self.motifs_path, self.motifs)
        return {"applied": applied, "rejected": rejected}

    def write_prompts(self, rows):
        """rows は {'id','name','no','hasArt'} の並び。絵が無いカードだけを出力する。"""
        lines = [STYLE]
        for row in sorted(rows, key=lambda r: r["no"]):
            motif = self.motifs.get(row["id"])
            if row["hasArt"] or not motif:
                continue
            lines.append("### %03d %s\n- 図柄: %s\n- ファイル名: %s.png\n" % (row["no"], row["name"], motif, file_stem(row["no"], row["id"])))
        save_atomic(self.prompts_path, "\n".join(lines))

    def card_prompt(self, row):
        motif = self.motifs.get(row["id"])
        if not motif:
            return {"state": "no-motif"}
        text = "%s\n### %03d %s\n- 図柄: %s\n- ファイル名: %s.png\n" % (STYLE, row["no"], row["name"], motif, file_stem(row["no"], row["id"]))
        return {"state": "ok", "text": text}
