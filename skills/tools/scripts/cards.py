"""収集結果から API 用のカード項目を組み立てる純粋関数。"""
import math
import re


def cost_of(chars):
    return max(1, min(10, math.ceil(chars / 2000)))


def first_sentence(text):
    match = re.search(r"^(.+?(?:。|\.\s))", text or "", re.S)
    sentence = match.group(1) if match else (text or "")
    return sentence.strip()


def is_japanese(text):
    """固有名詞の英字が混ざっても日本語の文として読めるなら真。"""
    japanese = len(re.findall(r"[぀-ヿ㐀-鿿]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return japanese > 0 and japanese * 4 >= latin


def triggers_of(description):
    match = re.search(r"トリガー", description or "")
    if not match:
        return []
    return re.findall(r"「([^」]{1,40})」", description[match.end():])[:12]


def rarity_map(counts, card_ids, known=True):
    """使用回数の順位で 1〜4 を返す。同数は同じ段階。0 回と不明は 1。"""
    result = {card_id: 1 for card_id in card_ids}
    if not known:
        return result
    total = len(card_ids)
    used = sorted({counts.get(c, 0) for c in card_ids if counts.get(c, 0) > 0}, reverse=True)
    for value in used:
        ahead = sum(1 for c in card_ids if counts.get(c, 0) > value)
        share = ahead / total
        stage = 4 if share < 0.10 else 3 if share < 0.30 else 2 if share < 0.60 else 1
        for card_id in card_ids:
            if counts.get(card_id, 0) == value:
                result[card_id] = stage
    return result


def summary_of(card, text, fresh):
    if fresh:
        return fresh["summary"]
    sentence = first_sentence(card["description"] or card.get("bodyHead", ""))
    if is_japanese(sentence):
        return sentence
    written = text.get("summary") if isinstance(text, dict) else None
    return written if isinstance(written, str) else ""


def build_card(card, entry, usage, texts, rarity, art_state, known):
    text = texts.get(card["id"])
    fresh = text if isinstance(text, dict) and text.get("sourceHash") == card["sourceHash"] else None
    return {
        "id": card["id"],
        "name": card["name"],
        "kind": card["kind"],
        "cost": cost_of(card["chars"]),
        "size": card["size"],
        "updated": card["mtime"],
        "triggers": triggers_of(card["description"]),
        "summary": summary_of(card, text, fresh),
        "flavor": fresh["flavor"] if fresh else None,
        "tools": card["tools"],
        "folder": entry["folder"],
        "no": entry["no"],
        "firstSeen": entry.get("firstSeen"),
        "packed": bool(entry.get("packed")),
        "count": usage["counts"].get(card["id"], 0) if known else None,
        "last": usage["last"].get(card["id"]) if known else None,
        "rarity": rarity[card["id"]],
        "hasArt": art_state["hasArt"],
        "hasPrev": art_state["hasPrev"],
        "parseError": bool(card["parseError"]),
    }


def build_detail(card):
    return {
        "id": card["id"],
        "argumentHint": card["argumentHint"],
        "files": card["files"],
        "path": card["path"],
    }
