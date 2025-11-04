from typing import Dict, Any
import json, re, os

def create_or_update_client(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "client_id": args.get("client_id") or "client_001", "saved": args}

def create_pending_consignment(args: Dict[str, Any]) -> Dict[str, Any]:
    consignment_id = args.get("consignment_id") or "2025-1300"
    return {"ok": True, "consignment_id": consignment_id, "status": "pending_created"}

def _pad3(n: int) -> str:
    return f"{n:03d}"

def assign_serials(args: Dict[str, Any]) -> Dict[str, Any]:
    year = int(args["year"]); invoice = int(args["invoice"])
    start = int(args.get("start_index", 1)); count = int(args["count"])
    serials = [f"{year}-{invoice}-{_pad3(i)}" for i in range(start, start+count)]
    pattern = re.compile(r"^\d{4}-\d+-\d{3}$")
    if not all(pattern.match(s) for s in serials):
        return {"ok": False, "error": "Serial format violation"}
    return {"ok": True, "serials": serials}

def lookup_cert(args: Dict[str, Any]) -> Dict[str, Any]:
    serial = args["serial"]
    path = os.path.join(os.getcwd(), "certs_stub.json")
    if not os.path.exists(path):
        return {"ok": False, "error": "certs_stub.json missing"}
    data = json.load(open(path))
    rec = data.get(serial)
    return {"ok": True, "found": bool(rec), "record": rec}
