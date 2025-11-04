from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
import os

app = FastAPI()

MODEL = os.getenv("MODEL", "gpt-4o-mini")

class ChatIn(BaseModel):
    message: str
    session_id: str | None = None

@app.get("/health")
def health():
    return {"ok": True, "model": MODEL}

@app.post("/chat")
def chat(payload: ChatIn, x_role: str = Header("staff")):
    msg = (payload.message or "").strip()
    if msg.lower().startswith("local:"):
        return {"text": msg[6:].strip() or "(ok)", "model": MODEL}
    return {"text": f"(stub) got: {msg}", "model": MODEL}

@app.get("/", response_class=HTMLResponse)
def root():
    html = (
        "<pre>"
        "SANGS Internal Agent\n"
        f"Model: {MODEL}\n"
        "Try: GET /health or POST /chat"
        "</pre>"
    )
    return HTMLResponse(html)
