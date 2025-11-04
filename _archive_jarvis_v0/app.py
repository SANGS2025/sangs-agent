from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os, json

from openai import OpenAI
import kb  # local KB helper

app = FastAPI()

# --- Config ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("MODEL", "gpt-4o-mini")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")  # set in .env; if blank, role-only admin allowed

client = OpenAI(api_key=OPENAI_API_KEY)

SANGS_PERSONA = (
    "You are the SANGS internal assistant. Be concise and accurate about SANGS "
    "grading/certification processes. If you don't know, say so."
)

# ------- Teach Mode (admin-only) -------
TEACH_SESSIONS: Dict[str, Dict[str, Any]] = {}  # session_id -> state

class ChatIn(BaseModel):
    message: str
    session_id: Optional[str] = None

class KBAdd(BaseModel):
    title: str
    content: str

class KBUpdate(BaseModel):
    id: str
    title: Optional[str] = None
    content: Optional[str] = None

def is_admin(x_role: str, x_admin_key: str) -> bool:
    role_ok = (x_role or "").lower() == "admin"
    if ADMIN_KEY:
        return role_ok and (x_admin_key == ADMIN_KEY)
    return role_ok

def require_admin(x_role: str, x_admin_key: str):
    if not is_admin(x_role, x_admin_key):
        raise HTTPException(status_code=403, detail="admin only")

def build_kb_context(user_query: str, k: int = 5) -> str:
    try:
        res = kb.search(user_query, k=k)
        matches: List[Dict[str, Any]] = res.get("matches", [])
        if not matches:
            return ""
        parts = []
        for m in matches:
            c = m.get("content","")
            if len(c) > 600:
                c = c[:600] + " …"
            parts.append(f"- [{m.get('id')}] {m.get('title')}: {c}")
        return "KB Context:\n" + "\n".join(parts)
    except Exception as e:
        return f"KB Context: (error accessing KB: {e})"

@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "kb_size": len(kb.list_entries().get("entries", []))}

@app.get("/kb")
def kb_list():
    return kb.list_entries()

@app.post("/kb/add")
def kb_add(payload: KBAdd, x_role: str = Header("staff"), x_admin_key: str = Header("", alias="X-Admin-Key")):
    require_admin(x_role, x_admin_key)
    out = kb.add_entry(payload.title, payload.content, tags=[])
    return out

@app.post("/kb/update")
def kb_update(payload: KBUpdate, x_role: str = Header("staff"), x_admin_key: str = Header("", alias="X-Admin-Key")):
    require_admin(x_role, x_admin_key)
    out = kb.update_entry(payload.id, payload.content, payload.title, tags=None)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out.get("error","unknown"))
    return out

def teach_handle(session_id: str, msg: str) -> str:
    """Admin-only: teach:new -> title -> content* -> teach:save / teach:cancel"""
    st = TEACH_SESSIONS.get(session_id)
    low = (msg or "").strip().lower()

    if low == "teach:new":
        TEACH_SESSIONS[session_id] = {"step": "title", "title": None, "content_parts": []}
        return "🧠 Teach Mode started. What is the *topic title* for this new knowledge entry?"

    if low == "teach:cancel":
        if st: TEACH_SESSIONS.pop(session_id, None)
        return "❌ Teach Mode cancelled."

    if low == "teach:save":
        if not st:
            return "No active Teach Mode session."
        if not st.get("title"):
            return "Missing title. Please send the topic title first."
        content = "\n".join(st.get("content_parts", [])) or "(empty)"
        out = kb.add_entry(st["title"], content, tags=[])
        TEACH_SESSIONS.pop(session_id, None)
        return f"✅ Knowledge saved: **{out['entry']['title']}** has been added to the internal database."

    if not st:
        return "No active Teach Mode. Send 'teach:new' to begin."

    if st["step"] == "title":
        st["title"] = (msg or "").strip()
        st["step"] = "content"
        return "Title set. What must I learn today? Send the knowledge in one or more messages; type 'teach:save' to store it."

    if st["step"] == "content":
        st["content_parts"].append((msg or "").strip())
        return "📝 Got it. You can send more info, or 'teach:save' to finalize."

    return "Unexpected state. Send 'teach:cancel' and 'teach:new' to restart."

@app.post("/chat")
def chat(payload: ChatIn, 
         x_role: str = Header("staff"), 
         x_admin_key: str = Header("", alias="X-Admin-Key")):
    msg = (payload.message or "").strip()
    sid = payload.session_id or "default"

    # Local bypass (no OpenAI call)
    if msg.lower().startswith("local:"):
        return JSONResponse({"text": msg[6:].strip() or "(ok)", "model": MODEL, "usage": None})

    # Admin Teach Mode
    if (msg.lower().startswith("teach:") or sid in TEACH_SESSIONS):
        require_admin(x_role, x_admin_key)
        return JSONResponse({"text": teach_handle(sid, msg), "model": MODEL, "usage": None})

    # Regular answering with KB context
    kb_context = build_kb_context(msg, k=5)
    sys_prompt = SANGS_PERSONA + ("\n\n" + kb_context if kb_context else "")

    try:
        resp = client.responses.create(
            model=MODEL,
            input=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": msg},
            ],
        )
        # text
        text = getattr(resp, "output_text", None)
        if not text:
            text = ""
            out = getattr(resp, "output", None) or []
            for part in out:
                if isinstance(part, dict):
                    c = part.get("content")
                    if isinstance(c, str):
                        text += c

        # usage (normalize if present)
        usage = None
        try:
            usage = getattr(resp, "usage", None)
            if usage and not isinstance(usage, dict):
                usage = {
                    "input_tokens": getattr(usage, "input_tokens", None) or getattr(usage, "input", None),
                    "output_tokens": getattr(usage, "output_tokens", None) or getattr(usage, "output", None),
                    "total_tokens": getattr(usage, "total_tokens", None) or None,
                }
        except Exception:
            usage = None

        return JSONResponse({"text": (text or "").strip() or "(no text)", "model": MODEL, "usage": usage})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# --- Minimal internal UI (works offline). Admin key typed in input when needed. ---
HTML_INDEX = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>SANGS Internal Chat</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    :root { --bg:#0b0f17; --card:#111827; --muted:#9ca3af; --fg:#e5e7eb; --acc:#60a5fa; --ok:#34d399; --err:#f87171; }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto}
    header{padding:18px 20px;border-bottom:1px solid #1f2937;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
    .badge{background:#0ea5e9; color:white; padding:2px 8px; border-radius:999px; font-size:12px}
    .wrap{max-width:980px;margin:0 auto;padding:18px}
    .panel{background:var(--card);border:1px solid #1f2937;border-radius:16px;overflow:hidden}
    .top{padding:14px;display:flex;gap:10px;align-items:center;border-bottom:1px solid #1f2937;flex-wrap:wrap}
    select,input[type=text],input[type=password]{background:#0b1220;border:1px solid #1f2937;border-radius:10px;color:var(--fg);padding:8px 10px}
    input[type=text]{flex:1;outline:none}
    button{background:var(--acc);color:#071521;border:0;border-radius:10px;padding:8px 12px;font-weight:600;cursor:pointer}
    button.ghost{background:#0b1220;color:var(--fg);border:1px solid #1f2937}
    #log{height:56vh;overflow:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
    .msg{padding:10px 12px;border-radius:12px;max-width:78%}
    .me {align-self:flex-end;background:#0b1220;border:1px solid #1f2937}
    .bot{align-self:flex-start;background:#0b1220;border:1px solid #1f2937}
    .small{font-size:12px;color:var(--muted)}
  </style>
</head>
<body>
<header>
  <div style="font-weight:700;font-size:18px">SANGS Internal Chat</div>
  <span id="modelBadge" class="badge"></span>
</header>

<div class="wrap">
  <div class="panel">
    <div class="top">
      <label class="small">Role</label>
      <select id="role">
        <option>staff</option>
        <option>admin</option>
      </select>
      <input id="adminKey" type="password" placeholder="X-Admin-Key (if set)" style="width:170px">
      <label class="small" style="margin-left:10px">Session</label>
      <input id="sid" type="text" placeholder="s1" value="s1" style="width:110px">
      <button class="ghost" onclick="teachNew()">Teach:new</button>
      <button class="ghost" onclick="teachSave()">Teach:save</button>
      <button class="ghost" onclick="teachCancel()">Teach:cancel</button>
      <button class="ghost" onclick="listKB()">List KB</button>
      <button onclick="localEcho()">Local echo</button>
    </div>
    <div id="log"></div>
    <div class="top">
      <input id="msg" type="text" placeholder="Type a message… (admin can teach:new/save/cancel)" onkeydown="if(event.key==='Enter'){send()}">
      <button onclick="send()">Send</button>
    </div>
  </div>
  <div class="small" style="margin-top:10px">
    This is an internal tool. KB updates require admin role (and Admin Key if configured).
  </div>
</div>

<script>
const MODEL_FROM_SERVER = "__MODEL__";
document.getElementById("modelBadge").textContent = MODEL_FROM_SERVER;

function add(kind, text){
  const d=document.createElement("div");
  d.className="msg "+kind; d.textContent=text;
  const log=document.getElementById("log"); log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
async function post(path, body){
  const role=document.getElementById("role").value;
  const adminKey=document.getElementById("adminKey").value || "";
  const res = await fetch(path, {
    method:"POST",
    headers: {
      "Content-Type":"application/json",
      "X-Role": role,
      "X-Admin-Key": adminKey
    },
    body: JSON.stringify(body)
  });
  const txt = await res.text();
  if (!res.ok) throw new Error(txt);
  try { return JSON.parse(txt); } catch(e){ throw new Error(txt); }
}
async function send(){
  const sid=document.getElementById("sid").value || "s1";
  const m=document.getElementById("msg").value.trim();
  if(!m) return;
  add("me", "You: "+m);
  document.getElementById("msg").value="";
  try{
    const out = await post("/chat", {message:m, session_id:sid});
    add("bot", "Bot: " + (out.text || JSON.stringify(out)));
  }catch(e){ add("bot", "Error: "+e.message); }
}
async function localEcho(){
  const sid=document.getElementById("sid").value || "s1";
  const m="local: hello";
  add("me", "You: "+m);
  try{
    const out = await post("/chat", {message:m, session_id:sid});
    add("bot", "Bot: " + (out.text || JSON.stringify(out)));
  }catch(e){ add("bot", "Error: "+e.message); }
}
async function listKB(){
  try{
    const res = await fetch("/kb"); const json = await res.json();
    add("bot", "KB entries: " + JSON.stringify(json.entries || json, null, 2));
  }catch(e){ add("bot", "Error: "+e.message); }
}
function teachNew(){ document.getElementById("msg").value="teach:new"; send(); }
function teachSave(){ document.getElementById("msg").value="teach:save"; send(); }
function teachCancel(){ document.getElementById("msg").value="teach:cancel"; send(); }
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(content=HTML_INDEX.replace("__MODEL__", MODEL))

# --- Label DB endpoints (append) ---
import kb_labels as labels
from pydantic import BaseModel
from fastapi import Header, HTTPException



from typing import Any, Dict, List, Optional

class LabelUpsertIn(BaseModel):
    id: str
    country: Optional[str] = None
    year: Optional[str] = None
    coin_name: Optional[str] = None
    addl1: Optional[str] = None
    addl2: Optional[str] = None
    aliases: Optional[List[str]] = []
    meta: Optional[Dict[str, Any]] = {}

class LabelLookupIn(BaseModel):
    query: str

@app.get("/labels/list")
def labels_list():
    """List all known label templates."""
    return labels.list_labels()

@app.post("/labels/upsert")
def labels_upsert(payload: LabelUpsertIn, x_role: str = Header("staff")):
    """Create/update a label template. Admin only."""
    if (x_role or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    # only persist non-null fields
    entry = {k: v for k, v in payload.dict().items() if v is not None}
    out = labels.upsert(entry)
    return out

@app.post("/labels/lookup")
def labels_lookup(payload: LabelLookupIn):
    """Fuzzy lookup by coin text, e.g. '1965 r1 english'."""
    q = (payload.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="empty query")
    return labels.lookup_label(q)



from typing import Any, Dict, List, Optional

class LabelAskIn(BaseModel):
    query: str
    limit: Optional[int] = 5

class LabelFormatIn(BaseModel):
    id: str
    serial_number: Optional[str] = "Insert Serial Number"
    grade1: Optional[str] = "Insert Grade 1"
    grade2: Optional[str] = "Insert Grade 2"

COLS8 = [
    "Serial Number","Grade 1","Grade 2","Country",
    "Year and Name","Additional Information",
    "Additional Information 2","Additional Information 3"
]

def _compose_year_and_name(entry: Dict[str, Any]) -> str:
    year = (entry.get("year") or "").strip()
    coin = (entry.get("coin_name") or "").strip()
    return " ".join(x for x in [year, coin] if x)

def _row8(entry: Dict[str, Any],
          serial="Insert Serial Number",
          g1="Insert Grade 1",
          g2="Insert Grade 2") -> List[str]:
    return [
        serial,
        g1,
        g2,
        entry.get("country") or "Insert Country",
        _compose_year_and_name(entry) or "Insert Year and Name",
        entry.get("addl1") or "Insert Additional Information 1",
        entry.get("addl2") or "Insert Additional Information 2",
        (entry.get("meta") or {}).get("addl3", "Insert Additional Information 3"),
    ]

def _markdown_table(rows: List[List[str]]) -> str:
    # Build a GitHub-flavoured Markdown table
    header = "| " + " | ".join(COLS8) + " |"
    sep    = "| " + " | ".join(["---"]*len(COLS8)) + " |"
    body   = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    return "\n".join([header, sep, body])

@app.post("/labels/ask")
def labels_ask(payload: LabelAskIn):
    """
    Plain-English lookup:
      - "label for 1965 R1"
      - "1965 R1 silver english"
      - "R1 Jan van Riebeeck"
    Returns multiple matches and a Markdown table preview in your 8-column layout.
    """
    out = labels.search_labels_nl(payload.query, limit=payload.limit or 5)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error","bad_query"))

    matches = out.get("matches", [])
    rows = [_row8(it) for it in matches] or []
    md = _markdown_table(rows) if rows else ""

    enriched = []
    for it, row in zip(matches, rows):
        enriched.append({
            "id": it.get("id"),
            "aliases": it.get("aliases", []),
            "country": it.get("country"),
            "year": it.get("year"),
            "coin_name": it.get("coin_name"),
            "addl1": it.get("addl1"),
            "addl2": it.get("addl2"),
            "row": row
        })
    return {"ok": True, "columns": COLS8, "results": enriched, "markdown_table": md}

@app.post("/labels/format")
def labels_format(payload: LabelFormatIn):
    """
    Given a known label id and (optional) serial/grade1/grade2,
    return the exact 8-column row + a one-row Markdown table.
    """
    res = labels.lookup_label(payload.id)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail="label_not_found")
    it = res.get("match") or {}
    row = _row8(
        it,
        serial=payload.serial_number or "Insert Serial Number",
        g1=payload.grade1 or "Insert Grade 1",
        g2=payload.grade2 or "Insert Grade 2",
    )
    md = _markdown_table([row])
    return {"ok": True, "columns": COLS8, "row": row, "markdown_table": md}



from typing import Any, Dict, List, Optional

@app.get("/ui", response_class=HTMLResponse)
def ui():
    html = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>SANGS Internal — Labels & Chat</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    :root { --bg:#0b0f17; --card:#111827; --muted:#9ca3af; --fg:#e5e7eb; --acc:#60a5fa; --ok:#34d399; --err:#f87171; }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto}
    header{padding:18px 20px;border-bottom:1px solid #1f2937;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
    .badge{background:#0ea5e9;color:#fff;border-radius:999px;padding:2px 8px;font-size:12px}
    .wrap{max-width:1080px;margin:0 auto;padding:16px}
    .panel{background:var(--card);border:1px solid #1f2937;border-radius:16px;overflow:hidden;margin-bottom:14px}
    .top{padding:12px;display:flex;gap:10px;align-items:center;border-bottom:1px solid #1f2937}
    select,input[type=text]{background:#0b1220;border:1px solid #1f2937;border-radius:10px;color:var(--fg);padding:8px 10px}
    input[type=text]{flex:1;outline:none}
    button{background:var(--acc);color:#071521;border:0;border-radius:10px;padding:8px 12px;font-weight:600;cursor:pointer}
    button.ghost{background:#0b1220;color:var(--fg);border:1px solid #1f2937}
    #log{height:46vh;overflow:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
    .msg{padding:10px 12px;border-radius:12px;max-width:78%}
    .me{align-self:flex-end;background:#0b1220;border:1px solid #1f2937}
    .bot{align-self:flex-start;background:#0b1220;border:1px solid #1f2937}
    .sys{align-self:center;color:var(--muted);font-size:13px}
    .footer{display:flex;gap:10px;padding:12px;border-top:1px solid #1f2937}
    .small{font-size:12px;color:var(--muted)}
    .section{padding:14px 14px 4px 14px;border-bottom:1px solid #1f2937;font-weight:600;color:#cbd5e1}
    table{border-collapse:collapse;width:100%;margin-top:10px}
    th,td{border:1px solid #1f2937;padding:8px 10px;text-align:left;font-size:14px}
    th{background:#0b1220}
    .row{padding:10px;border:1px dashed #1f2937;border-radius:10px;margin-top:10px}
    .pill{display:inline-flex;gap:6px;align-items:center;background:#0b1220;border:1px solid #1f2937;border-radius:999px;padding:6px 10px;font-size:12px;margin-right:6px}
    .flex{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
    .right{margin-left:auto}
  </style>
</head>
<body>
<header>
  <div style="font-weight:800;font-size:18px">SANGS Internal — Labels & Chat</div>
  <span class="badge" id="modelBadge">{MODEL}</span>
</header>

<div class="wrap">
  <!-- CHAT -->
  <div class="panel" id="chatPanel">
    <div class="section">Chat (type normal questions — or use <code>label: 1965 R1</code>)</div>
    <div class="top">
      <label class="small">Role</label>
      <select id="role"><option>staff</option><option>admin</option></select>
      <label class="small">Session</label>
      <input id="sid" placeholder="s1" value="s1" style="max-width:140px">
      <button class="ghost" onclick="teachNew()">Teach:new</button>
      <button class="ghost" onclick="teachSave()">Teach:save</button>
      <button class="ghost" onclick="teachCancel()">Teach:cancel</button>
      <button class="ghost" onclick="listKB()">List KB</button>
    </div>
    <div id="log"></div>
    <div class="footer">
      <input id="msg" type="text" placeholder="Try: label: 1965 R1  or  label: 1965 R1 silver english" onkeydown="if(event.key==='Enter'){send()}">
      <button onclick="send()">Send</button>
    </div>
  </div>

  <!-- RESULTS -->
  <div class="panel" id="resultsPanel">
    <div class="section">Label Results</div>
    <div id="results" style="padding:12px"></div>
  </div>

  <div class="small">Tip: in Label Results, click “Fill example” to preview a one-row worksheet output (Serial/Grade cells are editable).</div>
</div>

<script>
const MODEL_FROM_SERVER = "{MODEL}";
document.getElementById("modelBadge").textContent = MODEL_FROM_SERVER;

function add(kind, text){
  const d=document.createElement("div");
  d.className="msg "+kind; d.textContent=text;
  const log=document.getElementById("log"); log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
async function post(path, body, role){
  const res = await fetch(path, {
    method:"POST",
    headers: {"Content-Type":"application/json","X-Role": role||"staff"},
    body: JSON.stringify(body||{})
  });
  const txt = await res.text();
  if(!res.ok) throw new Error(txt);
  try { return JSON.parse(txt); } catch(e){ throw new Error(txt); }
}
function clearResults(){ document.getElementById("results").innerHTML=""; }
function cellEditable(td, value){
  td.contentEditable = true; td.textContent = value;
  td.addEventListener("input", ()=>{ /* stays editable for copy/paste */ });
}
function renderTable(columns, rows){
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  columns.forEach(c=>{ const th=document.createElement("th"); th.textContent=c; trh.appendChild(th); });
  thead.appendChild(trh);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  rows.forEach(r=>{
    const tr=document.createElement("tr");
    r.forEach((val, idx)=>{
      const td=document.createElement("td");
      // Make first 3 columns editable in UI for quick copy
      if(idx<=2) cellEditable(td, val||"");
      else td.textContent = val||"";
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  return table;
}
function renderMatches(payload){
  const root = document.getElementById("results"); clearResults();
  if(!payload || !payload.results || !payload.results.length){
    root.innerHTML = "<div class='small'>No matches.</div>"; return;
  }
  // Render a combined table with all rows (placeholder serial/grades)
  const rows = payload.results.map(r => r.row);
  root.appendChild(renderTable(payload.columns, rows));

  // Per-match cards with actions
  payload.results.forEach(r=>{
    const card = document.createElement("div"); card.className="row";
    card.innerHTML = `
      <div class="flex">
        <div><b>${r.id}</b></div>
        <div class="pill">${r.country||""}</div>
        <div class="pill">${(r.year||"")+" "+(r.coin_name||"")}</div>
        ${(r.aliases||[]).slice(0,4).map(a=>`<span class="pill">${a}</span>`).join("")}
        <div class="right">
          <button class="ghost" onclick="fillExample('${r.id.replace(/'/g, "\'")}')">Fill example</button>
        </div>
      </div>
    `;
    root.appendChild(card);
  });
}
async function fillExample(id){
  try{
    const out = await post("/labels/format", {
      id,
      serial_number: "Insert Serial Number",
      grade1: "Insert Grade 1",
      grade2: "Insert Grade 2"
    });
    const root = document.getElementById("results");
    root.appendChild(renderTable(out.columns, [out.row]));
  }catch(e){
    add("bot","Error: "+e.message);
  }
}
async function send(){
  const role=document.getElementById("role").value;
  const sid=document.getElementById("sid").value || "s1";
  const m=document.getElementById("msg").value.trim();
  if(!m) return;
  add("me","You: "+m);
  document.getElementById("msg").value="";

  // label: … = NL label lookup
  if(m.toLowerCase().startsWith("label:")){
    const q = m.slice(m.indexOf(":")+1).trim();
    try{
      const out = await post("/labels/ask", {query:q, limit:10}, role);
      add("bot","Bot: found "+(out.results||[]).length+" match(es).");
      renderMatches(out);
    }catch(e){ add("bot","Error: "+e.message); }
    return;
  }

  // default chat
  try{
    const out = await post("/chat", {message:m, session_id:sid}, role);
    add("bot","Bot: "+(out.text||JSON.stringify(out)));
  }catch(e){ add("bot","Error: "+e.message); }
}
function teachNew(){ document.getElementById("msg").value="teach:new"; send(); }
function teachSave(){ document.getElementById("msg").value="teach:save"; send(); }
function teachCancel(){ document.getElementById("msg").value="teach:cancel"; send(); }
async function listKB(){
  try{ const r = await fetch("/kb"); const j = await r.json();
       add("bot","KB entries: "+JSON.stringify(j.entries||j)); }
  catch(e){ add("bot","Error: "+e.message); }
}
</script>
</body>
</html>"""
    return HTMLResponse(content=html.replace("{MODEL}", MODEL))
