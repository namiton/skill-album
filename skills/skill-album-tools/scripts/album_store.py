"""album.json の検証と保存。サーバーだけが呼ぶ。"""
import datetime
import json
import os
import re
import shutil
import time

from atomic import save_json

VERSION = 1
UNCATEGORIZED = "uncategorized"
MAX_FOLDERS = 16
COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
FOLDER_ID_RE = re.compile(r"[a-z0-9-]{1,32}")

DEFAULT_FOLDERS = [
    ("dev", "開発", "#F4A3BE"),
    ("git", "Git・PR", "#F3C65E"),
    ("review", "レビュー・品質", "#A5D67C"),
    ("design", "UI・デザイン", "#8FB0EA"),
    ("docs", "文書・資料", "#C3A6E6"),
    ("data", "データ・分析", "#F4A3BE"),
    ("media", "画像・音声", "#F3C65E"),
    ("connect", "連携・自動化", "#A5D67C"),
    (UNCATEGORIZED, "未分類", "#D9CFD3"),
]


class StoreError(Exception):
    def __init__(self, reason, status=400):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def initial_folder(card):
    return UNCATEGORIZED


def _validate(data):
    if not isinstance(data, dict) or not isinstance(data.get("version"), int):
        raise ValueError("shape")
    if data["version"] > VERSION:
        return
    if not isinstance(data.get("revision"), int) or not isinstance(data.get("nextNo"), int):
        raise ValueError("shape")
    if not isinstance(data.get("folders"), list) or not isinstance(data.get("cards"), dict):
        raise ValueError("shape")
    ids = set()
    for folder in data["folders"]:
        if not isinstance(folder, dict) or not isinstance(folder.get("id"), str):
            raise ValueError("folder")
        ids.add(folder["id"])
    if UNCATEGORIZED not in ids:
        raise ValueError("folder")
    for card in data["cards"].values():
        if not isinstance(card, dict) or card.get("folder") not in ids or not isinstance(card.get("no"), int):
            raise ValueError("card")


MIGRATIONS = {}


def migrate(data):
    while data["version"] < VERSION:
        data = MIGRATIONS[data["version"]](data)
    return data


def new_album():
    return {
        "version": VERSION,
        "revision": 0,
        "sound": True,
        "nextNo": 1,
        "lastCounts": {},
        "folders": [
            {"id": fid, "name": name, "color": color, "order": index}
            for index, (fid, name, color) in enumerate(DEFAULT_FOLDERS)
        ],
        "cards": {},
    }


class AlbumStore:
    def __init__(self, data_dir, today=None):
        self.path = os.path.join(data_dir, "album.json")
        self.bak = self.path + ".bak"
        self.data_dir = data_dir
        self.today = today or (lambda: datetime.date.today().isoformat())
        self.readonly = False
        self.notice = None
        self.existed = os.path.exists(self.path)
        self.data = self._load()

    def _read(self, path):
        """読めないときの OSError は呼び出し元へ伝え、壊れた内容だけ ValueError にする。"""
        for attempt in range(6):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05)
        try:
            _validate(data)
        except KeyError as exc:
            raise ValueError("shape") from exc
        return data

    def _load(self):
        try:
            data = self._read(self.path)
        except FileNotFoundError:
            try:
                data = self._read(self.bak)
                self.notice = "restored"
            except FileNotFoundError:
                return new_album()
            except ValueError:
                return self._reset(self.bak)
        except ValueError:
            try:
                data = self._read(self.bak)
                self.notice = "restored"
            except (FileNotFoundError, ValueError):
                return self._reset(self.path)
        if data["version"] > VERSION:
            self.readonly = True
            self.notice = "readonly"
            return data
        return migrate(data)

    def _reset(self, broken):
        """壊れた内容を退避できたときだけ初期状態にする。退避できなければ起動を止める。"""
        stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copyfile(broken, os.path.join(self.data_dir, "album.broken-%s.json" % stamp))
        self.notice = "reset"
        return new_album()

    def _save(self, draft, bump=True):
        """保存に成功してからメモリへ反映する。"""
        if self.readonly:
            raise StoreError("readonly", 409)
        if bump:
            draft["revision"] += 1
        _validate(draft)
        save_json(self.path, draft)
        save_json(self.bak, draft)
        self.data = draft

    def folder_ids(self):
        return {f["id"] for f in self.data["folders"]}

    def sync(self, scanned):
        """新しいカードを登録し、前回より減った収集元を返す。"""
        draft = json.loads(json.dumps(self.data))
        cards = draft["cards"]
        first = not draft["lastCounts"] and not cards
        changed = False
        for card in scanned["cards"]:
            if card["id"] in cards:
                continue
            folder = initial_folder(card)
            if folder not in self.folder_ids():
                folder = UNCATEGORIZED
            cards[card["id"]] = {
                "folder": folder,
                "by": "auto",
                "firstSeen": None if first else self.today(),
                "no": draft["nextNo"],
            }
            if not first:
                cards[card["id"]]["packed"] = True
            draft["nextNo"] += 1
            changed = True
        lost = [s for s, n in draft["lastCounts"].items() if n > 0 and scanned["counts"].get(s, 0) == 0]
        counts = dict(draft["lastCounts"])
        for source, number in scanned["counts"].items():
            if number > 0 or source not in counts:
                counts[source] = number
        if counts != draft["lastCounts"]:
            draft["lastCounts"] = counts
            changed = True
        if changed:
            if self.readonly:
                self.data = draft
            else:
                self._save(draft, bump=False)
        return lost

    def update(self, payload):
        if self.readonly:
            raise StoreError("readonly", 409)
        if not isinstance(payload, dict) or set(payload) - {"revision", "sound", "folders", "cards"}:
            raise StoreError("schema")
        if payload.get("revision") != self.data["revision"]:
            raise StoreError("revision", 409)
        draft = json.loads(json.dumps(self.data))
        if "sound" in payload:
            if not isinstance(payload["sound"], bool):
                raise StoreError("schema")
            draft["sound"] = payload["sound"]
        if "folders" in payload:
            self._apply_folders(draft, payload["folders"])
        if "cards" in payload:
            self._apply_cards(draft, payload["cards"])
        self._save(draft)
        return self.data

    def _apply_folders(self, draft, folders):
        if not isinstance(folders, list) or len(folders) > MAX_FOLDERS:
            raise StoreError("folders-limit")
        seen = set()
        clean = []
        for index, folder in enumerate(folders):
            if not isinstance(folder, dict) or set(folder) - {"id", "name", "color", "order"}:
                raise StoreError("schema")
            fid, name, color = folder.get("id"), folder.get("name"), folder.get("color")
            if not isinstance(fid, str) or not FOLDER_ID_RE.fullmatch(fid) or fid in seen:
                raise StoreError("folder-id")
            if not isinstance(name, str) or not 1 <= len(name) <= 32 or "\n" in name:
                raise StoreError("folder-name")
            if not isinstance(color, str) or not COLOR_RE.fullmatch(color):
                raise StoreError("folder-color")
            if fid == UNCATEGORIZED and name != "未分類":
                raise StoreError("folder-name")
            seen.add(fid)
            clean.append({"id": fid, "name": name, "color": color, "order": index})
        if UNCATEGORIZED not in seen:
            raise StoreError("folder-id")
        draft["folders"] = clean
        for card in draft["cards"].values():
            if card["folder"] not in seen:
                card["folder"] = UNCATEGORIZED
                card["by"] = "auto"

    def _apply_cards(self, draft, cards):
        if not isinstance(cards, dict):
            raise StoreError("schema")
        ids = {f["id"] for f in draft["folders"]}
        for card_id, change in cards.items():
            if card_id not in draft["cards"]:
                raise StoreError("unknown-card")
            if not isinstance(change, dict) or set(change) - {"folder", "no", "firstSeen", "by"}:
                raise StoreError("schema")
            target = draft["cards"][card_id]
            if "folder" in change:
                if change["folder"] not in ids:
                    raise StoreError("unknown-folder")
                if change["folder"] != target["folder"]:
                    target["folder"] = change["folder"]
                    target["by"] = "manual"

    def open_pack(self, ids):
        if self.readonly:
            raise StoreError("readonly", 409)
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise StoreError("schema")
        draft = json.loads(json.dumps(self.data))
        opened = 0
        for card_id in ids:
            card = draft["cards"].get(card_id)
            if card is not None and card.pop("packed", False):
                opened += 1
        if opened:
            self._save(draft, bump=False)
        return {"opened": opened}

    def auto_assign(self, plan):
        if self.readonly:
            raise StoreError("readonly", 409)
        if not isinstance(plan, dict):
            raise StoreError("schema")
        applied = 0
        rejected = []
        ids = self.folder_ids()
        draft = json.loads(json.dumps(self.data))
        for card_id, folder in plan.items():
            card = draft["cards"].get(card_id)
            if card is None:
                rejected.append({"id": str(card_id)[:64], "reason": "unknown-card"})
            elif folder not in ids:
                rejected.append({"id": card_id, "reason": "unknown-folder"})
            elif card.get("by") == "manual":
                rejected.append({"id": card_id, "reason": "manual"})
            else:
                card["folder"] = folder
                applied += 1
        if applied:
            self._save(draft)
        return {"applied": applied, "rejected": rejected}
