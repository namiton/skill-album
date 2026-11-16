"""一時ファイルと os.replace による原子保存。"""
import json
import os
import tempfile
import time


def save_atomic(path, data):
    payload = data.encode("utf-8") if isinstance(data, str) else data
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        replace_with_retry(temp, path)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def replace_with_retry(source, target):
    for attempt in range(6):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05)


def save_json(path, value):
    save_atomic(path, json.dumps(value, ensure_ascii=False, indent=1))
