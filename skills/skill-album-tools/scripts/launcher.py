"""サーバーを切り離して起動し、結果を 1 行の JSON で返す短命のスクリプト。"""
import argparse
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import paths  # noqa: E402

SESSION_RE = re.compile(r"[0-9a-fA-F-]{36}")
APP = "skill-album"
VERSION = "1"
DEFAULT_PORT = 47851


def request(port, method, path, timeout=1.0):
    """プロキシを通さず 127.0.0.1 へ問い合わせる。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        headers = {"Origin": "http://127.0.0.1:%d" % port}
        if method in ("POST", "PUT"):
            headers["Content-Length"] = "0"
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def ping(port):
    """{'state': 'none'|'ours'|'old'|'other'} を返す。"""
    try:
        status, body = request(port, "GET", "/api/ping")
    except http.client.HTTPException:
        return "other"
    except OSError:
        return "none"
    try:
        data = json.loads(body)
    except ValueError:
        return "other"
    if status != 200 or not isinstance(data, dict) or data.get("app") != APP:
        return "other"
    return "ours" if data.get("version") == VERSION else "old"


def port_is_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def spawn(port, data_dir, claude_home):
    os.makedirs(data_dir, exist_ok=True)
    argv = [sys.executable, "-I", os.path.join(HERE, "server.py"), "--port", str(port), "--data-dir", data_dir]
    if claude_home:
        argv += ["--claude-home", claude_home]
    if sys.platform == "win32":
        detach = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        detach = {"start_new_session": True}
    subprocess.Popen(
        argv,
        **detach,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        cwd=data_dir,
    )


def wait_for(port, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = ping(port)
        if state != "none":
            return state
        time.sleep(0.1)
    return "none"


def ensure(port, data_dir, claude_home=None, wait=8.0):
    state = ping(port)
    if state == "old":
        try:
            request(port, "POST", "/api/shutdown")
        except OSError:
            pass
        deadline = time.time() + 3
        while time.time() < deadline and ping(port) != "none":
            time.sleep(0.1)
        state = ping(port)
        if state == "old":
            return "port-busy"
    if state == "other":
        return "port-busy"
    if state == "none":
        spawn(port, data_dir, claude_home)
        state = wait_for(port, wait)
        if state == "none" and not port_is_open(port):
            return "failed"
    return "ok" if state == "ours" else "port-busy"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--session")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", default=paths.data_dir())
    parser.add_argument("--claude-home")
    args = parser.parse_args(argv)
    if args.session is not None and not SESSION_RE.fullmatch(args.session):
        print(json.dumps({"status": "bad-session"}))
        return 2
    status = ensure(args.port, args.data_dir, args.claude_home)
    url = None
    if status == "ok":
        url = "http://127.0.0.1:%d/" % args.port
        if args.session:
            url += "?s=" + args.session
        if not args.no_browser:
            webbrowser.open(url)
    print(json.dumps({"status": status, "port": args.port, "url": url}))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
