from __future__ import annotations
from typing import Dict, Any, List
import json, os, re

DB_PATH = os.path.join(os.path.dirname(__file__), "kb_labels.json")

def _ensure_db() -> Dict[str, Any]:
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w") as f:
            json.dump({"labels": []}, f, indent=2)
    with open(DB_PATH, "r") as f:
        return json.load(f)

def _save_db(db: Dict[str, Any]) -> None:
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def list_labels() -> Dict[str, Any]:
    db = _ensure_db()
    return {"ok": True, "labels": db.get("labels", [])}

def normalize_query(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s

def lookup_label(query: str) -> Dict[str, Any]:
    db = _ensure_db()
    if not query.strip():
        return {"ok": False, "error": "empty_query"}
    qn = normalize_query(query)

    # exact id first
    for it in db["labels"]:
        if normalize_query(it["id"]) == qn:
            return {"ok": True, "match": it, "note": "exact"}

    # then aliases or fuzzy contains
    cands: List[Dict[str, Any]] = []
    for it in db["labels"]:
        hay = " ".join([it["id"]] + (it.get("aliases") or []))
        if normalize_query(hay).find(qn) >= 0:
            cands.append(it)
    if cands:
        return {"ok": True, "match": cands[0], "note": "fuzzy"}

    return {"ok": False, "error": "not_found"}

def upsert(entry: Dict[str, Any]) -> Dict[str, Any]:
    db = _ensure_db()
    pos = None
    for i, it in enumerate(db["labels"]):
        if it["id"] == entry["id"]:
            pos = i
            break
    if pos is None:
        db["labels"].append(entry)
    else:
        db["labels"][pos] = entry
    _save_db(db)
    return {"ok": True, "entry": entry}


# --- fuzzy multi-match search (added) ---
import re
from typing import List, Dict, Any

def _nl_norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+"," ", (s or "").lower()).strip()

def _nl_score(qtoks: List[str], it: Dict[str, Any]) -> int:
    hay = " ".join(filter(None, [
        it.get("id",""),
        " ".join(it.get("aliases", [])),
        it.get("country",""),
        it.get("year",""),
        it.get("coin_name",""),
        it.get("addl1",""),
        it.get("addl2",""),
        " ".join([str(v) for v in (it.get("meta") or {}).values()]),
    ]))
    hset = set(_nl_norm(hay).split())
    return sum(1 for t in qtoks if t in hset)

def search_labels_nl(query: str, limit: int = 5) -> Dict[str, Any]:
    q = _nl_norm(query)
    if not q:
        return {"ok": False, "error": "empty_query"}
    qtoks = q.split()
    db = _ensure_db()
    scored = []
    for it in db["labels"]:
        sc = _nl_score(qtoks, it)
        if sc > 0:
            scored.append((sc, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = [it for _, it in scored[:limit]]
    return {"ok": True, "matches": out, "count": len(out)}
