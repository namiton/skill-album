"""起動から /api/cards が 200 を返すまでの秒数を測る。"""
import argparse
import http.client
import os
import socket
import subprocess
import sys
import time

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude-home", required=True)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    port = free_port()
    started = time.time()
    proc = subprocess.Popen([sys.executable, "-I", os.path.join(SCRIPTS, "server.py"), "--port", str(port), "--claude-home", args.claude_home, "--data-dir", args.data_dir])
    try:
        while time.time() - started < 15:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/cards")
                status = conn.getresponse().status
                conn.close()
                if status == 200:
                    print("startup_seconds=%.2f" % (time.time() - started))
                    return 0
            except OSError:
                time.sleep(0.05)
        print("timeout")
        return 1
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
