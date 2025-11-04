import os, json, time, re
from typing import List, Dict, Any

KB_PATH = os.path.join(os.getcwd(), "kb", "kb_store.json")

def _load() -> Dict[str, Any]:
    if not os.path.exists(KB_PATH):
        os.makedirs(os.path.dirname(KB_PATH), exist_ok=True)
        json.dump({"entries":[]}, open(KB_PATH,"w"))
    return json.load(open(KB_PATH))

def _save(data: Dict[str, Any]):
    json.dump(data, open(KB_PATH,"w"), ensure_ascii=False, indent=2)

def list_entries() -> Dict[str, Any]:
    return _load()

def add_entry(title: str, content: str, tags: List[str] | None = None) -> Dict[str, Any]:
    data = _load()
    next_id = f"kb-{len(data['entries'])+1:04d}"
    entry = {
        "id": next_id,
        "title": title.strip(),
        "content": content.strip(),
        "tags": tags or [],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    data["entries"].append(entry)
    _save(data)
    return {"ok": True, "entry": entry}

def update_entry(entry_id: str, content: str | None = None, title: str | None = None, tags: List[str] | None = None) -> Dict[str, Any]:
    data = _load()
    for e in data["entries"]:
        if e["id"] == entry_id:
            if title is not None: e["title"] = title.strip()
            if content is not None: e["content"] = content.strip()
            if tags is not None: e["tags"] = tags
            e["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _save(data)
            return {"ok": True, "entry": e}
    return {"ok": False, "error": "not_found"}

def search(query: str, k: int = 5) -> Dict[str, Any]:
    data = _load()
    q = query.lower()
    def score(e):
        txt = (e["title"] + " " + e["content"] + " " + " ".join(e.get("tags",[]))).lower()
        hits = sum(len(re.findall(w, txt)) for w in re.findall(r"\w+", q) if len(w) > 2)
        return hits
    ranked = sorted(data["entries"], key=score, reverse=True)[:k]
    return {"ok": True, "matches": ranked}
