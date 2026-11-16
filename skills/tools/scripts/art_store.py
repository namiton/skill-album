"""カード絵の検証と保存。"""
import os
import urllib.parse

from atomic import replace_with_retry, save_atomic

MAX_BYTES = 10 * 1024 * 1024
TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}


class ArtError(Exception):
    def __init__(self, reason, status=400):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def sniff(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def art_filename(card_id):
    """`:` が NTFS の代替データストリームにならないようにする。"""
    name = urllib.parse.quote(card_id, safe="")
    return "_" + name if name.upper() in RESERVED else name


class ArtStore:
    def __init__(self, data_dir):
        self.art = os.path.join(data_dir, "art")
        self.prev = os.path.join(self.art, "prev")
        self.revision = 0
        self._recover()

    def _recover(self):
        """中断で残った作業ファイルを片付ける。現行の絵が無ければ .swap を戻す。"""
        if not os.path.isdir(self.art):
            return
        for name in os.listdir(self.art):
            path = os.path.join(self.art, name)
            if name.endswith(".new"):
                os.remove(path)
            elif name.endswith(".swap"):
                original = path[: -len(".swap")]
                stem = os.path.splitext(os.path.basename(original))[0]
                others = [n for n in os.listdir(self.art) if os.path.splitext(n)[0] == stem and n.rsplit(".", 1)[-1] in TYPES]
                if others:
                    os.remove(path)
                else:
                    replace_with_retry(path, original)

    def _find(self, folder, card_id):
        base = art_filename(card_id)
        for ext in TYPES:
            path = os.path.join(folder, base + "." + ext)
            if os.path.isfile(path):
                return path
        return None

    def state(self, card_id):
        return {"hasArt": self._find(self.art, card_id) is not None, "hasPrev": self._find(self.prev, card_id) is not None}

    def read(self, card_id):
        path = self._find(self.art, card_id)
        if not path:
            return None
        with open(path, "rb") as fh:
            return fh.read(), TYPES[path.rsplit(".", 1)[1]]

    def _to_prev(self, card_id, current):
        os.makedirs(self.prev, exist_ok=True)
        old = self._find(self.prev, card_id)
        target = os.path.join(self.prev, os.path.basename(current))
        replace_with_retry(current, target)
        if old and old != target:
            os.remove(old)

    def _copy_to_prev(self, card_id, current):
        """現行の絵を残したまま直前の 1 枚を作る。"""
        old = self._find(self.prev, card_id)
        target = os.path.join(self.prev, os.path.basename(current))
        with open(current, "rb") as fh:
            save_atomic(target, fh.read())
        if old and old != target:
            os.remove(old)

    def put(self, card_id, data, if_absent=False):
        if len(data) > MAX_BYTES:
            raise ArtError("too-large", 413)
        ext = sniff(data)
        if ext is None:
            raise ArtError("format")
        current = self._find(self.art, card_id)
        if current and if_absent:
            raise ArtError("exists", 409)
        target = os.path.join(self.art, art_filename(card_id) + "." + ext)
        os.makedirs(self.art, exist_ok=True)
        staged = target + ".new"
        save_atomic(staged, data)
        if current:
            self._copy_to_prev(card_id, current)
        replace_with_retry(staged, target)
        if current and current != target:
            os.remove(current)
        self.revision += 1

    def delete(self, card_id):
        current = self._find(self.art, card_id)
        if current:
            self._to_prev(card_id, current)
            self.revision += 1

    def restore(self, card_id):
        old = self._find(self.prev, card_id)
        if not old:
            raise ArtError("no-prev", 404)
        current = self._find(self.art, card_id)
        os.makedirs(self.art, exist_ok=True)
        swap = None
        if current:
            swap = current + ".swap"
            replace_with_retry(current, swap)
        try:
            replace_with_retry(old, os.path.join(self.art, os.path.basename(old)))
        except OSError:
            if swap:
                replace_with_retry(swap, current)
            raise
        if swap:
            replace_with_retry(swap, os.path.join(self.prev, os.path.basename(current)))
        self.revision += 1
