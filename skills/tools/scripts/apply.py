"""Claude の作業用 CLI。コマンドは open / export / classify / texts / prompts に固定する。"""
import argparse
import http.client
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import launcher  # noqa: E402
import paths  # noqa: E402

ROUTES = {"classify": "/api/auto-assign", "texts": "/api/texts", "prompts": "/api/motifs"}


def call(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        headers = {"Origin": "http://127.0.0.1:%d" % port}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["open", "export", "classify", "texts", "prompts"])
    parser.add_argument("target", nargs="?")
    parser.add_argument("--port", type=int, default=launcher.DEFAULT_PORT)
    parser.add_argument("--data-dir", default=paths.data_dir())
    parser.add_argument("--claude-home")
    args = parser.parse_args(argv)
    if args.command == "open":
        forwarded = ["--port", str(args.port), "--data-dir", args.data_dir]
        if args.claude_home:
            forwarded += ["--claude-home", args.claude_home]
        return launcher.main(forwarded)
    if launcher.ensure(args.port, args.data_dir, args.claude_home) != "ok":
        print(json.dumps({"status": "port-busy"}))
        return 1
    if args.command == "export":
        if args.target not in ("classify", "texts", "prompts"):
            parser.error("export には classify / texts / prompts を指定する")
        status, data = call(args.port, "GET", "/api/export?kind=" + args.target)
    else:
        if not args.target:
            parser.error("案の JSON ファイルを指定する")
        with open(args.target, encoding="utf-8") as fh:
            plan = json.load(fh)
        status, data = call(args.port, "POST", ROUTES[args.command], plan)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
