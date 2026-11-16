"""テスト用の Claude 環境とサーバーの足場。実環境には触れない。"""
import http.client
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server as server_module  # noqa: E402

ORG = "aaaaaaaa-0000-0000-0000-000000000000"
SESSION = "11111111-2222-3333-4444-555555555555"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def skill(name=None, description="説明文です。次の文。", extra=""):
    head = "---\n"
    if name:
        head += "name: %s\n" % name
    head += "description: %s\n%s---\n\n# 本文\n手順の本文。\n" % (description, extra)
    return head


def build_home(root):
    """user 2 枚、synced 1 枚（旧組織 1 枚は対象外）、plugin 3 枚の配置を作る。"""
    home = os.path.join(root, ".claude")
    write(os.path.join(home, "skills", "alpha", "SKILL.md"), skill())
    write(os.path.join(home, "skills", "alpha", "scripts", "a.py"), "print(1)\n")
    write(os.path.join(home, "skills", "beta", "SKILL.md"), skill("beta-name", "説明。トリガー「ベータ」「試す」", "allowed-tools: Read, Bash\nargument-hint: \"[x]\"\n"))
    new = os.path.join(home, "skills", "synced", ORG + "_new")
    write(os.path.join(new, "manifest.json"), json.dumps({"lastUpdated": 1}))
    write(os.path.join(new, "pdf", "SKILL.md"), skill("pdf"))
    old = os.path.join(home, "skills", "synced", "bbbbbbbb-0000-0000-0000-000000000000_old")
    write(os.path.join(old, "manifest.json"), json.dumps({"lastUpdated": 9}))
    write(os.path.join(old, "stale", "SKILL.md"), skill("stale"))
    cached = os.path.join(home, "plugins", "cache", "m", "git-ops", "1.0.0")
    write(os.path.join(cached, "skills", "commit", "SKILL.md"), skill())
    market = os.path.join(home, "plugins", "local", "wiki")
    write(os.path.join(market, ".claude-plugin", "marketplace.json"), json.dumps({"plugins": [{"name": "wiki", "source": "./plugins/wiki"}]}))
    write(os.path.join(market, "plugins", "wiki", "skills", "wiki", "SKILL.md"), skill())
    write(os.path.join(home, "plugins", "cache", "m", "off", "1.0.0", "skills", "nope", "SKILL.md"), skill())
    write(os.path.join(home, "plugins", "installed_plugins.json"), json.dumps({"plugins": {
        "git-ops@m": [{"scope": "user", "installPath": cached}, {"scope": "project", "installPath": cached}],
        "wiki@wiki": [{"installPath": os.path.join(home, "plugins", "cache", "wiki", "missing")}],
        "off@m": [{"installPath": os.path.join(home, "plugins", "cache", "m", "off", "1.0.0")}],
    }}))
    write(os.path.join(home, "settings.json"), json.dumps({
        "secret": "must-not-appear",
        "enabledPlugins": {"git-ops@m": True, "wiki@wiki": True, "off@m": False},
        "extraKnownMarketplaces": {"wiki": {"source": {"source": "directory", "path": market}}},
    }))
    write(os.path.join(root, ".claude.json"), json.dumps({"oauthAccount": {"organizationUuid": ORG}}))
    write(os.path.join(home, ".env", "secret.md"), "TOKEN=must-not-appear\n")
    return home


class Harness:
    def __init__(self, root, web_dir=None, opener=None):
        self.home = build_home(root)
        self.data = os.path.join(root, "data")
        self.web = web_dir or os.path.join(root, "web")
        os.makedirs(self.web, exist_ok=True)
        write(os.path.join(self.web, "index.html"), "<!DOCTYPE html>")
        write(os.path.join(self.web, "a.js"), "export {}")
        write(os.path.join(self.web, "secret.txt"), "no")
        self.opened = []
        self.app = server_module.App(self.home, self.data, self.web, os.path.join(root, ".claude.json"), opener=opener or self.opened.append)
        self.server = server_module.make_server(self.app, 0)
        self.port = self.server.server_address[1]
        self.app.refresh()
        self.app.usage.state = "ok"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            base = {"Origin": "http://127.0.0.1:%d" % self.port} if method != "GET" else {}
            base.update(headers or {})
            conn.request(method, path, body=body, headers=base)
            response = conn.getresponse()
            data = response.read()
            return response.status, data, dict(response.getheaders())
        finally:
            conn.close()

    def json(self, method, path, value=None):
        body = None if value is None else json.dumps(value).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if body is None and method in ("POST", "PUT"):
            headers["Content-Length"] = "0"
        status, data, _ = self.raw(method, path, body, headers)
        return status, json.loads(data or b"{}")

    def cards(self):
        return {card["id"]: card for card in self.json("GET", "/api/cards")[1]["cards"]}
