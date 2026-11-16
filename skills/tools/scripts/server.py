"""Skill Album のローカル HTTP サーバー。"""
import argparse
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import album_store  # noqa: E402
import paths  # noqa: E402
from album_store import AlbumStore, StoreError  # noqa: E402
from art_store import MAX_BYTES, ArtError, ArtStore  # noqa: E402
from atomic import save_atomic  # noqa: E402
import cards as card_builder  # noqa: E402
import scan as scanner  # noqa: E402
from text_store import TextStore  # noqa: E402
from usage import UsageTracker  # noqa: E402

APP = "skill-album"
VERSION = "1"
DEFAULT_PORT = 47851
JSON_LIMIT = 1024 * 1024
IDLE_SECONDS = 600
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; media-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".woff2": "font/woff2",
}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
SESSION_RE = re.compile(r"[0-9a-fA-F-]{36}")
MAX_SAFE = 2 ** 53 - 1


def open_folder(folder):
    if sys.platform == "win32":
        os.startfile(folder)
        return
    command = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([command, folder], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class App:
    def __init__(self, claude_home, data_dir, web_dir, dot_json=None, opener=None, idle=IDLE_SECONDS):
        self.claude_home = claude_home
        self.data_dir = data_dir
        self.web_dir = web_dir
        self.dot_json = dot_json
        self.opener = opener or open_folder
        self.idle = idle
        self.lock = threading.RLock()
        os.makedirs(data_dir, exist_ok=True)
        self.album = AlbumStore(data_dir)
        self.art = ArtStore(data_dir)
        self.texts = TextStore(data_dir)
        self.usage = UsageTracker(os.path.join(claude_home, "projects"), data_dir)
        self.last_access = time.time()
        self.last_seq = 0
        self.scanned = None
        self.signature = None
        self.cards_rev = 0
        self.lost = []
        self.server = None

    def refresh(self, force=True):
        """収集して album.json へ登録する。リクエストからは呼ばない。"""
        with self.lock:
            scanned = scanner.scan(self.claude_home, self.dot_json)
            signature = [(c["id"], c["sourceHash"]) for c in scanned["cards"]]
            if signature != self.signature:
                self.signature = signature
                self.cards_rev += 1
            self.scanned = scanned
            self.lost = self.album.sync(scanned)
            return scanned

    def snapshot(self):
        with self.lock:
            return self.scanned or {"cards": [], "counts": {}, "diagnostics": []}

    def card_index(self):
        return {card["id"]: card for card in self.snapshot()["cards"]}

    def start_usage(self):
        def work():
            try:
                self.usage.scan()
            except Exception:
                self.usage.state = "unreadable"
                traceback.print_exc(limit=1)

        threading.Thread(target=work, daemon=True).start()

    def usage_payload(self):
        summary = self.usage.summary()
        return {"state": self.usage.state, "progress": self.usage.progress, "startedAt": summary["startedAt"], "counts": summary["counts"], "last": summary["last"]}

    def card_list(self):
        with self.lock:
            scanned = self.snapshot()
            summary = self.usage.summary()
            known = self.usage.state != "unreadable"
            ids = [card["id"] for card in scanned["cards"]]
            rarity = card_builder.rarity_map(summary["counts"], ids, known and self.usage.state == "ok")
            entries = self.album.data["cards"]
            rows = []
            for card in scanned["cards"]:
                entry = entries.get(card["id"])
                if entry is None:
                    entry = {"folder": album_store.UNCATEGORIZED, "no": 0, "firstSeen": None}
                count_known = known and self.usage.state == "ok"
                rows.append(card_builder.build_card(card, entry, summary, self.texts.texts, rarity, self.art.state(card["id"]), count_known))
            diagnostics = {}
            for item in scanned["diagnostics"]:
                diagnostics[item["kind"]] = diagnostics.get(item["kind"], 0) + 1
            return {
                "cards": rows,
                "revision": self.album.data["revision"],
                "maxNo": self.album.data["nextNo"] - 1,
                "diagnostics": diagnostics,
                "lost": self.lost,
                "notice": self.album.notice,
                "readonly": self.album.readonly,
                "firstRun": not self.album.existed,
            }

    def state(self):
        return {
            "version": VERSION,
            "albumRevision": self.album.data["revision"],
            "cardsRev": self.cards_rev,
            "textsRev": self.texts.revision,
            "artRev": self.art.revision,
            "usage": {"state": self.usage.state, "progress": self.usage.progress},
        }

    def export(self, kind):
        index = self.card_index()
        if kind == "classify":
            return {
                "folders": [{"id": f["id"], "name": f["name"]} for f in self.album.data["folders"]],
                "cards": [
                    {"id": c["id"], "description": c["description"][:600]}
                    for c in index.values()
                    if self.album.data["cards"].get(c["id"], {}).get("by") != "manual"
                ],
            }
        if kind == "texts":
            out = []
            for card in index.values():
                current = self.texts.texts.get(card["id"])
                if not (isinstance(current, dict) and current.get("sourceHash") == card["sourceHash"]):
                    out.append({"id": card["id"], "sourceHash": card["sourceHash"], "path": card["path"]})
            return {"cards": out}
        if kind == "prompts":
            return {
                "cards": [
                    {"id": c["id"], "description": c["description"][:600]}
                    for c in index.values()
                    if c["id"] not in self.texts.motifs and not self.art.state(c["id"])["hasArt"]
                ]
            }
        raise StoreError("kind")

    def prompt_rows(self):
        rows = []
        for card in self.card_index().values():
            entry = self.album.data["cards"].get(card["id"], {})
            rows.append({"id": card["id"], "name": card["name"], "no": entry.get("no", 0), "hasArt": self.art.state(card["id"])["hasArt"]})
        return rows

    def request_use(self, card_id, session):
        with self.lock:
            self.last_seq = max(self.last_seq + 1, int(time.time() * 1000))
            payload = {"seq": self.last_seq, "id": card_id}
            save_atomic(os.path.join(self.data_dir, "inbox", session + ".json"), json.dumps(payload))
            return self.last_seq

    def use_result(self, session, seq):
        try:
            with open(os.path.join(self.data_dir, "outbox", session + ".json"), encoding="utf-8") as fh:
                row = json.load(fh)
        except (OSError, ValueError):
            return "pending"
        if not isinstance(row, dict) or row.get("seq") != seq:
            return "pending"
        return "filled" if row.get("isFilled") is True else "refused"

    def clean_handoff(self, days=7):
        cutoff = time.time() - days * 86400
        for folder in ("inbox", "outbox", "usage-inbox"):
            path = os.path.join(self.data_dir, folder)
            if os.path.isdir(path):
                for name in os.listdir(path):
                    full = os.path.join(path, name)
                    try:
                        if os.path.getmtime(full) < cutoff:
                            os.remove(full)
                    except OSError:
                        pass

    def watch(self, stop):
        previous = time.time()
        while not stop.wait(5):
            now = time.time()
            if now - previous > 30:
                self.last_access = now
            previous = now
            if now - self.last_access > self.idle:
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            try:
                self.refresh()
                self.usage.ingest_typed(set(self.card_index()))
            except Exception:
                traceback.print_exc(limit=1)


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "SkillAlbum"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    app = None

    def log_message(self, *args):
        pass

    def _reply(self, status, body=b"", kind="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, value):
        self._reply(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def _deny(self, status, reason=None):
        self.close_connection = True
        self._json(status, {"reason": reason} if reason else {})

    def _guard(self):
        port = self.server.server_address[1]
        if self.headers.get("Host") != "127.0.0.1:%d" % port:
            self._deny(403)
            return None
        origin = self.headers.get("Origin")
        expected = "http://127.0.0.1:%d" % port
        if (self.command != "GET" and origin != expected) or (origin is not None and origin != expected):
            self._deny(403)
            return None
        raw = self.headers.get("Content-Length")
        if len(self.headers.get_all("Content-Length") or []) > 1:
            self._deny(400)
            return None
        if raw is None:
            if self.command in ("POST", "PUT"):
                self._deny(411)
                return None
            length = 0
        elif not re.fullmatch(r"[0-9]{1,12}", raw):
            self._deny(400)
            return None
        else:
            length = int(raw)
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        is_art = self.command == "PUT" and self.path.split("?")[0].endswith("/art")
        if length > (MAX_BYTES if is_art else JSON_LIMIT):
            self._deny(413, "too-large")
            return None
        if length > 0:
            allowed = IMAGE_TYPES if is_art else {"application/json"}
            if kind not in allowed:
                self._deny(415)
                return None
        return length

    def _body(self, length):
        return self.rfile.read(length) if length else b""

    def _json_body(self, length):
        try:
            return json.loads(self._body(length) or b"{}")
        except ValueError:
            raise StoreError("json")

    def _handle(self):
        app = self.app
        app.last_access = time.time()
        length = self._guard()
        if length is None:
            return
        parts = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parts.query)
        try:
            if parts.path.startswith("/api/"):
                self._api(parts.path, query, length)
            elif self.command == "GET":
                self._static(parts.path)
            else:
                self._deny(404)
        except StoreError as exc:
            self._read_rest(length)
            self._json(exc.status, {"reason": exc.reason})
        except ArtError as exc:
            self._json(exc.status, {"reason": exc.reason})
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc(limit=2)
            self.close_connection = True
            self._reply(500)

    def _read_rest(self, length):
        pass

    def _static(self, path):
        name = "index.html" if path == "/" else urllib.parse.unquote(path).lstrip("/")
        ext = os.path.splitext(name)[1].lower()
        if ext not in MIME or "\\" in name or ".." in name.split("/"):
            return self._deny(404)
        full = os.path.join(self.app.web_dir, *name.split("/"))
        base = os.path.realpath(self.app.web_dir)
        if not os.path.realpath(full).startswith(base + os.sep) or not os.path.isfile(full):
            return self._deny(404)
        with open(full, "rb") as fh:
            self._reply(200, fh.read(), MIME[ext])

    def _session(self, query):
        value = query.get("s", [""])[0]
        if not SESSION_RE.fullmatch(value):
            raise StoreError("session")
        return value

    def _api(self, path, query, length):
        app = self.app
        method = self.command
        route = path[len("/api/"):]
        if method == "GET" and route == "ping":
            return self._json(200, {"app": APP, "version": VERSION})
        if method == "GET" and route == "state":
            return self._json(200, app.state())
        if method == "GET" and route == "cards":
            return self._json(200, app.card_list())
        if method == "GET" and route == "usage":
            return self._json(200, app.usage_payload())
        if method == "GET" and route == "album":
            return self._json(200, app.album.data)
        if method == "GET" and route == "export":
            return self._json(200, app.export(query.get("kind", [""])[0]))
        if method == "GET" and route == "use-result":
            session = self._session(query)
            raw = query.get("seq", [""])[0]
            if not re.fullmatch(r"[1-9][0-9]{0,15}", raw) or int(raw) > MAX_SAFE:
                raise StoreError("seq")
            return self._json(200, {"state": app.use_result(session, int(raw))})
        if method == "PUT" and route == "album":
            data = self._json_body(length)
            with app.lock:
                try:
                    return self._json(200, app.album.update(data))
                except StoreError as exc:
                    if exc.status == 409:
                        return self._json(409, {"reason": exc.reason})
                    raise
        if method == "POST" and route == "auto-assign":
            data = self._json_body(length)
            with app.lock:
                return self._json(200, app.album.auto_assign(data))
        if method == "POST" and route == "pack/open":
            data = self._json_body(length)
            if not isinstance(data, dict):
                raise StoreError("schema")
            with app.lock:
                return self._json(200, app.album.open_pack(data.get("ids")))
        if method == "POST" and route == "texts":
            data = self._json_body(length)
            self._no_readonly()
            with app.lock:
                return self._json(200, app.texts.apply_texts(data, app.card_index()))
        if method == "POST" and route == "motifs":
            data = self._json_body(length)
            self._no_readonly()
            with app.lock:
                result = app.texts.apply_motifs(data, app.card_index())
                app.texts.write_prompts(app.prompt_rows())
                return self._json(200, result)
        if method == "POST" and route == "shutdown":
            self._json(200, {})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        match = re.fullmatch(r"cards/([^/]+)(/.*)?", route)
        if not match:
            return self._deny(404)
        card_id = urllib.parse.unquote(match.group(1))
        tail = match.group(2) or ""
        index = app.card_index()
        card = index.get(card_id)
        if card is None:
            return self._deny(404)
        if method == "GET" and tail == "":
            with open(card["path"], encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            detail = card_builder.build_detail(card)
            detail["body"] = body
            return self._json(200, detail)
        if method == "POST" and tail == "/open":
            self._body(length)
            folder = card["dir"]
            if not os.path.isdir(folder):
                return self._deny(404)
            app.opener(folder)
            return self._json(200, {})
        if method == "POST" and tail == "/use":
            self._body(length)
            return self._json(200, {"seq": app.request_use(card_id, self._session(query))})
        if method == "GET" and tail == "/prompt":
            entry = app.album.data["cards"].get(card_id, {})
            return self._json(200, app.texts.card_prompt({"id": card_id, "name": card["name"], "no": entry.get("no", 0)}))
        if tail == "/art":
            if method == "GET":
                found = app.art.read(card_id)
                return self._reply(200, found[0], found[1]) if found else self._deny(404)
            if method in ("PUT", "DELETE"):
                data = self._body(length)
                self._no_readonly()
                with app.lock:
                    if method == "PUT":
                        app.art.put(card_id, data, query.get("ifAbsent", [""])[0] == "1")
                    else:
                        app.art.delete(card_id)
                return self._json(200, app.art.state(card_id))
        if method == "POST" and tail == "/art/restore":
            self._body(length)
            self._no_readonly()
            with app.lock:
                app.art.restore(card_id)
            return self._json(200, app.art.state(card_id))
        return self._deny(404)

    def _no_readonly(self):
        if self.app.album.readonly:
            raise StoreError("readonly", 409)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()


class ExclusiveServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(app, port=DEFAULT_PORT):
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ExclusiveServer(("127.0.0.1", port), handler)
    app.server = server
    return server


def bind(port, retry_seconds=3):
    """データに触る前に bind する。負けたプロセスはログも台帳も変更しない。"""
    handler = type("BoundHandler", (Handler,), {"app": None})
    deadline = time.time() + retry_seconds
    while True:
        try:
            return ExclusiveServer(("127.0.0.1", port), handler), handler
        except OSError:
            if time.time() > deadline:
                raise
            time.sleep(0.1)


def run(app, server):
    app.server = server
    app.clean_handoff()
    app.refresh()
    app.start_usage()
    stop = threading.Event()
    threading.Thread(target=app.watch, args=(stop,), daemon=True).start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


def default_paths():
    return paths.claude_home(), paths.data_dir()


def main(argv=None):
    home, data = default_paths()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--claude-home", default=home)
    parser.add_argument("--data-dir", default=data)
    args = parser.parse_args(argv)
    try:
        server, handler = bind(args.port)
    except OSError:
        return 3
    os.makedirs(args.data_dir, exist_ok=True)
    log = open(os.path.join(args.data_dir, "server.log"), "w", encoding="utf-8")
    sys.stderr = log
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    web = os.path.join(root, "web")
    try:
        app = App(args.claude_home, args.data_dir, web)
    except OSError:
        traceback.print_exc(limit=1)
        server.server_close()
        return 4
    handler.app = app
    run(app, server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
