"""会話履歴からの利用回数の取り込み。Skill 名、日時、呼び出し id のハッシュだけを保存する。"""
import glob
import hashlib
import json
import os
import re
import threading

from atomic import save_json

SCAN_VERSION = 1
SAMPLE_LINES = 50
UNREADABLE_MIN = 20
DEDUPE_SECONDS = 30
SKILL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
SESSION_RE = re.compile(r"[0-9a-fA-F-]{36}")


def extract_events(line):
    """1 行から Skill のツール呼び出しを取り出す。input.skill 以外は読まない。"""
    try:
        row = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(row, dict) or not isinstance(row.get("message"), dict):
        return []
    content = row["message"].get("content")
    if not isinstance(content, list):
        return []
    events = []
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill"):
            continue
        tool_id = block.get("id")
        tool_input = block.get("input")
        skill = tool_input.get("skill") if isinstance(tool_input, dict) else None
        if isinstance(tool_id, str) and isinstance(skill, str) and SKILL_RE.fullmatch(skill):
            events.append({
                "id": hashlib.sha256(tool_id.encode()).hexdigest()[:16],
                "skill": skill,
                "ts": row.get("timestamp") if isinstance(row.get("timestamp"), str) else "",
            })
    return events


def _recognized(line):
    try:
        row = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(row, dict) and isinstance(row.get("message"), dict) and "content" in row["message"]


def _seconds(stamp):
    import datetime

    try:
        return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


class UsageTracker:
    def __init__(self, projects_dir, data_dir):
        self.projects_dir = projects_dir
        self.data_dir = data_dir
        self.ledger = os.path.join(data_dir, "usage-events.jsonl")
        self.scan_path = os.path.join(data_dir, "usage-scan.json")
        self.inbox = os.path.join(data_dir, "usage-inbox")
        self.lock = threading.RLock()
        self.state = "scanning"
        self.progress = 0
        self.events = {}
        self._trim_tail()
        self._load_ledger()
        self.scan_info = self._load_scan()

    def _trim_tail(self):
        try:
            with open(self.ledger, "rb+") as fh:
                data = fh.read()
                if data and not data.endswith(b"\n"):
                    fh.truncate(data.rfind(b"\n") + 1)
        except OSError:
            pass

    def _load_ledger(self):
        try:
            with open(self.ledger, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        event = json.loads(line)
                        self.events[event["id"]] = event
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            pass

    def _load_scan(self):
        try:
            with open(self.scan_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("version") == SCAN_VERSION and isinstance(data.get("files"), dict):
                data.setdefault("typed", {})
                return data
        except (OSError, ValueError, AttributeError):
            pass
        return {"version": SCAN_VERSION, "files": {}, "typed": {}}

    def _append(self, events):
        fresh = [e for e in events if e["id"] not in self.events]
        if not fresh:
            return
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.ledger, "a", encoding="utf-8") as fh:
            for event in fresh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()
        for event in fresh:
            self.events[event["id"]] = event

    def scan(self):
        paths = glob.glob(os.path.join(glob.escape(self.projects_dir), "*", "*.jsonl"))
        paths += glob.glob(os.path.join(glob.escape(self.projects_dir), "*", "*", "subagents", "*.jsonl"))
        lines_read = 0
        recognized = 0
        changed = 0
        failed = 0
        tool_lines = 0
        for index, path in enumerate(paths):
            try:
                stat = os.stat(path)
                known = self.scan_info["files"].get(path)
                if known and known[0] == stat.st_size and known[1] == stat.st_mtime:
                    continue
                changed += 1
                events = []
                sampled = 0
                file_lines = 0
                file_recognized = 0
                with open(path, "rb") as fh:
                    for raw in fh:
                        if not raw.strip():
                            continue
                        file_lines += 1
                        if sampled < SAMPLE_LINES:
                            sampled += 1
                            file_recognized += 1 if _recognized(raw) else 0
                        if b'"tool_use"' in raw:
                            tool_lines += 1
                        if b'"Skill"' in raw:
                            events.extend(extract_events(raw))
                lines_read += file_lines
                recognized += file_recognized
                with self.lock:
                    self._append(events)
                    if file_lines == 0 or file_recognized > 0:
                        self.scan_info["files"][path] = [stat.st_size, stat.st_mtime]
            except OSError:
                failed += 1
                continue
            self.progress = int((index + 1) * 100 / max(1, len(paths)))
            if changed % 100 == 0:
                self._save_scan()
        no_history = not paths and not self.events
        unknown_lines = lines_read >= UNREADABLE_MIN and recognized == 0
        all_failed = changed > 0 and failed == changed
        renamed_tool = not self.events and tool_lines >= UNREADABLE_MIN
        if renamed_tool:
            self.scan_info["files"] = {}
        self._save_scan()
        self.progress = 100
        self.state = "unreadable" if no_history or unknown_lines or all_failed or renamed_tool else "ok"

    def _save_scan(self):
        """管理情報の保存失敗は次回の再走査で取り戻せるので、集計を止めない。"""
        with self.lock:
            try:
                save_json(self.scan_path, self.scan_info)
            except OSError:
                pass

    def ingest_typed(self, known):
        """Mod が書いた手入力の呼び出しのうち、既知のカードだけを台帳へ取り込む。"""
        if not os.path.isdir(self.inbox):
            return
        changed = False
        for name in os.listdir(self.inbox):
            session = name[:-5] if name.endswith(".json") else ""
            if not SESSION_RE.fullmatch(session):
                continue
            try:
                with open(os.path.join(self.inbox, name), encoding="utf-8") as fh:
                    row = json.load(fh)
                seq, skill = row["seq"], row["skill"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if not (isinstance(seq, int) and seq > 0 and isinstance(skill, str) and SKILL_RE.fullmatch(skill)):
                continue
            if seq <= self.scan_info["typed"].get(session, 0):
                continue
            import datetime

            stamp = datetime.datetime.fromtimestamp(seq / 1000, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            key = hashlib.sha256(("typed:%s:%d" % (session, seq)).encode()).hexdigest()[:16]
            with self.lock:
                if skill in known:
                    self._append([{"id": key, "skill": skill, "ts": stamp, "typed": True}])
                self.scan_info["typed"][session] = seq
            changed = True
        if changed:
            self._save_scan()

    def summary(self):
        """{'counts': {...}, 'last': {...}, 'startedAt': ...} を返す。手入力とツールの二重計上を除く。"""
        with self.lock:
            events = list(self.events.values())
        tools = {}
        for event in events:
            if not event.get("typed"):
                moment = _seconds(event.get("ts"))
                if moment is not None:
                    tools.setdefault(event["skill"], []).append(moment)
        counts = {}
        last = {}
        started = None
        for event in events:
            stamp = event.get("ts") or ""
            if event.get("typed"):
                moment = _seconds(stamp)
                nearby = tools.get(event["skill"], [])
                if moment is not None and any(abs(moment - t) <= DEDUPE_SECONDS for t in nearby):
                    continue
            counts[event["skill"]] = counts.get(event["skill"], 0) + 1
            if stamp:
                if stamp > last.get(event["skill"], ""):
                    last[event["skill"]] = stamp
                started = min(started, stamp) if started else stamp
        return {"counts": counts, "last": last, "startedAt": started}
