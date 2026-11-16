"""SKILL.md の frontmatter を読む。キーと値、引用符、ブロック記法、リストだけに対応する。"""
import re

_KEY = re.compile(r"([A-Za-z][A-Za-z0-9_-]*):(?:\s+(.*))?$")


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            return inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return inner.replace("''", "'")
    return value


def _block(lines, style):
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    margin = min(indents) if indents else 0
    stripped = [line[margin:] if line.strip() else "" for line in lines]
    if style.startswith("|"):
        return "\n".join(stripped).strip("\n")
    return re.sub(r"\s*\n\s*", " ", "\n".join(stripped)).strip()


def parse_frontmatter(text):
    """{'data': dict, 'body': str, 'error': None か理由} を返す。例外は出さない。"""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {"data": {}, "body": text, "error": "no-frontmatter"}
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {"data": {}, "body": text, "error": "unterminated"}
    data = {}
    index = 1
    while index < end:
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            continue
        match = _KEY.match(line)
        if not match:
            return {"data": data, "body": "\n".join(lines[end + 1:]), "error": "unparsable"}
        key, value = match.group(1), (match.group(2) or "").strip()
        children = []
        while index < end and (not lines[index].strip() or lines[index][0] in " \t-"):
            children.append(lines[index])
            index += 1
        if re.fullmatch(r"[|>][+-]?", value):
            data[key] = _block(children, value)
        elif not value and any(c.strip().startswith("- ") for c in children):
            data[key] = [_unquote(c.strip()[2:]) for c in children if c.strip().startswith("- ")]
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [_unquote(part) for part in value[1:-1].split(",") if part.strip()]
        else:
            continuation = " ".join(c.strip() for c in children if c.strip())
            data[key] = _unquote((value + " " + continuation).strip())
    return {"data": data, "body": "\n".join(lines[end + 1:]), "error": None}
