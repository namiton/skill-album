"""Skill の収集。ファイルへは書かない。"""
import hashlib
import json
import os
import re

from frontmatter import parse_frontmatter

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MAX_FILES = 200


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _inside(path, roots):
    real = os.path.normcase(os.path.realpath(path))
    for root in roots:
        base = os.path.normcase(os.path.realpath(root))
        if real == base or real.startswith(base + os.sep):
            return True
    return False


def _list_files(folder):
    names = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return []
    for entry in entries:
        names.append(entry)
        child = os.path.join(folder, entry)
        if os.path.isdir(child) and not os.path.islink(child):
            try:
                for inner in sorted(os.listdir(child)):
                    names.append(entry + "/" + inner)
            except OSError:
                pass
        if len(names) >= MAX_FILES:
            break
    return names[:MAX_FILES]


def _make_card(folder, prefix, kind, source, roots, use_name=True):
    path = os.path.join(folder, "SKILL.md")
    if not os.path.isfile(path) or not _inside(path, roots):
        return None
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", "replace")
    parsed = parse_frontmatter(text)
    data = parsed["data"]
    dirname = os.path.basename(folder.rstrip("\\/"))
    name = data.get("name") if use_name else None
    if not (isinstance(name, str) and NAME_RE.fullmatch(name)):
        name = dirname
    if not NAME_RE.fullmatch(name):
        return {"invalid": True, "source": source}
    description = data.get("description")
    tools = data.get("allowed-tools", [])
    if isinstance(tools, str):
        tools = [part.strip() for part in tools.split(",") if part.strip()]
    stat = os.stat(path)
    return {
        "id": prefix + name,
        "name": name,
        "kind": kind,
        "source": source,
        "dir": folder,
        "path": path,
        "description": description if isinstance(description, str) else "",
        "tools": [t for t in tools if isinstance(t, str)],
        "argumentHint": data.get("argument-hint") if isinstance(data.get("argument-hint"), str) else "",
        "chars": len(text),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "sourceHash": hashlib.sha256(raw).hexdigest(),
        "parseError": parsed["error"],
        "bodyHead": parsed["body"].strip()[:120],
        "files": _list_files(folder),
    }


def current_org(dot_json):
    node = _read_json(dot_json).get("oauthAccount")
    if isinstance(node, dict) and isinstance(node.get("organizationUuid"), str):
        return node["organizationUuid"]
    return None


def _choose_synced(root, org):
    if not os.path.isdir(root):
        return None
    folders = [os.path.join(root, n) for n in os.listdir(root) if os.path.isdir(os.path.join(root, n))]

    def updated(folder):
        value = _read_json(os.path.join(folder, "manifest.json")).get("lastUpdated")
        return value if isinstance(value, (int, float)) else 0

    pool = [f for f in folders if os.path.basename(f).split("_")[0] == org] if org else folders
    return max(pool, key=updated) if pool else None


def _plugin_root(key, installed, marketplaces, home):
    plugin, _, market = key.partition("@")
    entries = installed.get(key)
    if isinstance(entries, list):
        for entry in entries:
            path = entry.get("installPath") if isinstance(entry, dict) else None
            if isinstance(path, str) and os.path.isdir(path):
                return path, None
    known = marketplaces.get(market)
    base = known.get("source", {}).get("path") if isinstance(known, dict) and isinstance(known.get("source"), dict) else None
    if isinstance(base, str):
        manifest = _read_json(os.path.join(base, ".claude-plugin", "marketplace.json"))
        for item in manifest.get("plugins", []) if isinstance(manifest.get("plugins"), list) else []:
            if isinstance(item, dict) and item.get("name") == plugin and isinstance(item.get("source"), str):
                path = os.path.normpath(os.path.join(base, item["source"]))
                if os.path.isdir(path):
                    return path, base
    return None, None


def scan(claude_home, dot_json=None):
    """{'cards': [...], 'counts': {...}, 'diagnostics': [...]} を返す。"""
    if not dot_json:
        inside = os.path.join(os.path.abspath(claude_home), ".claude.json")
        dot_json = inside if os.path.isfile(inside) else os.path.join(os.path.dirname(os.path.abspath(claude_home)), ".claude.json")
    cards = {}
    counts = {"user": 0, "synced": 0, "plugins": 0}
    diagnostics = []

    def add(folder, prefix, kind, source, allowed, use_name=True):
        """1 枚の失敗で同じ収集元の残りを失わないように隔離する。"""
        try:
            card = _make_card(folder, prefix, kind, source, allowed, use_name)
        except Exception as exc:
            diagnostics.append({"kind": "card-error", "source": source, "error": type(exc).__name__})
            return
        if card is None:
            return
        if card.get("invalid"):
            diagnostics.append({"kind": "invalid-name", "source": source})
            return
        if card["id"] in cards:
            diagnostics.append({"kind": "duplicate-id", "source": source})
            return
        cards[card["id"]] = card
        counts[source] += 1

    def guarded(source, work):
        try:
            work()
        except Exception as exc:
            diagnostics.append({"kind": "source-error", "source": source, "error": type(exc).__name__})

    roots = [claude_home]

    def users():
        root = os.path.join(claude_home, "skills")
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                folder = os.path.join(root, name)
                if name != "synced" and os.path.isdir(folder):
                    add(folder, "", "自作", "user", roots)

    def synced():
        chosen = _choose_synced(os.path.join(claude_home, "skills", "synced"), current_org(dot_json))
        if chosen:
            for name in sorted(os.listdir(chosen)):
                folder = os.path.join(chosen, name)
                if os.path.isdir(folder):
                    add(folder, "anthropic-skills:", "公式", "synced", roots, use_name=False)

    def plugins():
        settings = _read_json(os.path.join(claude_home, "settings.json"))
        installed = _read_json(os.path.join(claude_home, "plugins", "installed_plugins.json")).get("plugins", {})
        enabled = settings.get("enabledPlugins", {})
        marketplaces = settings.get("extraKnownMarketplaces", {})
        for key in sorted(enabled if isinstance(enabled, dict) else {}):
            if enabled[key] is not True:
                continue
            plugin = key.partition("@")[0]
            root, extra = _plugin_root(key, installed if isinstance(installed, dict) else {}, marketplaces if isinstance(marketplaces, dict) else {}, claude_home)
            if root is None:
                diagnostics.append({"kind": "plugin-unresolved", "source": "plugins"})
                continue
            allowed = roots + [extra or root]
            base = os.path.join(root, "skills")
            if os.path.isdir(base):
                for name in sorted(os.listdir(base)):
                    folder = os.path.join(base, name)
                    if os.path.isdir(folder):
                        add(folder, plugin + ":", "プラグイン", "plugins", allowed)

    for source, work in (("user", users), ("synced", synced), ("plugins", plugins)):
        guarded(source, work)
    return {"cards": sorted(cards.values(), key=lambda c: c["id"]), "counts": counts, "diagnostics": diagnostics}
