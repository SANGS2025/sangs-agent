# ~/sangs-agent/consignments.py
from typing import Optional, List, Tuple

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from db import pool  # use the shared pool
from auth import require_user

router = APIRouter(prefix="/consignments", tags=["consignments"])

class ConsignmentCreateIn(BaseModel):
    submission_no: str = Field(min_length=3)
    pedigree_mode: str = Field(default="none", pattern="^(none|per_consignment|per_coin)$")
    pedigree_value: Optional[str] = None

class ConsignmentOut(BaseModel):
    id: int
    number: str
    pedigree_mode: str
    pedigree_value: Optional[str] = None

class ItemAddIn(BaseModel):
    grade1: Optional[str] = None
    grade2: Optional[str] = None
    country: str
    year_and_name: str
    addl1: Optional[str] = None
    addl2: Optional[str] = None
    addl3: Optional[str] = None
    pedigree_override: Optional[str] = None

class ItemOut(BaseModel):
    id: int
    serial_number: str
    position_idx: int
    grade1: Optional[str] = None
    grade2: Optional[str] = None
    country: Optional[str] = None
    year_and_name: Optional[str] = None
    addl1: Optional[str] = None
    addl2: Optional[str] = None
    addl3: Optional[str] = None
    label_type: Optional[str] = None

def _fetch_consignment(consignment_id: int):
    sql = "SELECT id, number, pedigree_mode, pedigree_value FROM consignments WHERE id=%s"
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (consignment_id,))
        return cur.fetchone()

def _next_position_idx(consignment_id: int) -> int:
    sql = "SELECT COALESCE(MAX(position_idx), 0) FROM consignment_items WHERE consignment_id=%s"
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (consignment_id,))
        (mx,) = cur.fetchone()
        return int(mx) + 1

def _present(x) -> bool:
    return bool(x and str(x).strip())

def _compute_label_type(grade1, grade2, year_and_name, addl1, addl2, addl3) -> str:
    g2 = _present(grade2)
    a_count = sum(1 for v in (addl1, addl2, addl3) if _present(v))
    if not g2:
        if a_count <= 1:
            return "Simple"
        elif a_count == 2:
            return "Simple +"
        else:
            return "Simple ++"
    else:
        if a_count <= 1:
            return "Double"
        else:
            return "Double +"

def _place_pedigree_into_addls(addl1, addl2, addl3, pedigree_val: Optional[str]):
    pv = (pedigree_val or "").strip()
    if not pv:
        return addl1, addl2, addl3
    a1 = (addl1 or "").strip()
    a2 = (addl2 or "").strip()
    a3 = (addl3 or "").strip()
    if not a1:
        return pv, addl2, addl3
    if not a2:
        return addl1, pv, addl3
    if not a3:
        return addl1, addl2, pv
    return addl1, addl2, addl3

def _serial_for(number: str, idx: int) -> str:
    return f"{number}-{idx:03d}"

_FAMILY_ALIASES = {"UNC": "MS"}
def _norm_family(token: str) -> str:
    t = (token or "").upper().strip()
    if not t:
        return "UNK"
    return _FAMILY_ALIASES.get(t, t)

def _extract_family_and_number(grade1: Optional[str]) -> Tuple[str, Optional[int]]:
    g1 = (grade1 or "").upper().replace("UNC", "MS").strip()
    if not g1:
        return ("UNK", None)
    letters = "".join(ch for ch in g1 if ch.isalpha())
    digits = "".join(ch for ch in g1 if ch.isdigit())
    fam = _norm_family(letters or "UNK")
    num: Optional[int] = None
    if digits:
        try:
            num = int(digits)
        except ValueError:
            num = None
    return (fam, num)

def _make_coin_key(country: Optional[str], year: Optional[str], coin_name: Optional[str], addl1: Optional[str]) -> str:
    def nz(v: Optional[str]) -> str:
        return (v or "").strip()
    return "|".join([nz(country), nz(year), nz(coin_name), nz(addl1)])

def _split_year_and_name(year_and_name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    s = (year_and_name or "").strip()
    if not s:
        return (None, None)
    parts = s.split()
    if parts and parts[0].isdigit():
        return (parts[0], " ".join(parts[1:]).strip() or None)
    return (None, s)

@router.post("", response_model=ConsignmentOut)
def create_consignment(payload: ConsignmentCreateIn, user=Depends(require_user)):
    if payload.pedigree_mode == "per_consignment" and not (payload.pedigree_value or "").strip():
        raise HTTPException(status_code=400, detail="pedigree_value required for per_consignment")

    sql = """
    INSERT INTO consignments (number, pedigree_mode, pedigree_value)
    VALUES (%s, %s, %s)
    ON CONFLICT (number) DO UPDATE
      SET pedigree_mode = EXCLUDED.pedigree_mode,
          pedigree_value = EXCLUDED.pedigree_value
    RETURNING id, number, pedigree_mode, pedigree_value
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (payload.submission_no, payload.pedigree_mode, payload.pedigree_value))
        return cur.fetchone()

@router.post("/{consignment_id}/items", response_model=ItemOut)
def add_item(consignment_id: str, payload: ItemAddIn, user=Depends(require_user)):
    cid = _get_consignment_id(consignment_id)
    if cid is None:
        raise HTTPException(status_code=404, detail=f"Consignment not found: {consignment_id}")
    cons = _fetch_consignment(cid)
    if not cons:
        raise HTTPException(status_code=404, detail="consignment not found")

    idx = _next_position_idx(cid)
    serial = _serial_for(cons["number"], idx)

    pedigree_val = None
    if cons["pedigree_mode"] == "per_consignment":
        pedigree_val = cons["pedigree_value"]
    elif cons["pedigree_mode"] == "per_coin":
        pedigree_val = payload.pedigree_override

    addl1, addl2, addl3 = _place_pedigree_into_addls(payload.addl1, payload.addl2, payload.addl3, pedigree_val)
    label_type = _compute_label_type(payload.grade1, payload.grade2, payload.year_and_name, addl1, addl2, addl3)

    ins = """
    INSERT INTO consignment_items (
      consignment_id, position_idx, serial_number,
      grade1, grade2, country, year_and_name,
      addl1, addl2, addl3, label_type
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    RETURNING id, serial_number, position_idx, grade1, grade2, country, year_and_name, addl1, addl2, addl3, label_type
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(ins, (
            cid, idx, serial,
            payload.grade1, payload.grade2, payload.country, payload.year_and_name,
            addl1, addl2, addl3, label_type
        ))
        return cur.fetchone()

def _get_consignment_id(identifier: str) -> Optional[int]:
    """Get consignment ID from either an integer ID or a consignment number."""
    # Try as integer first
    try:
        return int(identifier)
    except ValueError:
        pass
    # Try as consignment number
    sql = "SELECT id FROM consignments WHERE number=%s LIMIT 1"
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (identifier,))
        row = cur.fetchone()
        if row:
            return row[0]
    return None

@router.get("/{consignment_id}/items", response_model=List[ItemOut])
def list_items(consignment_id: str, user=Depends(require_user)):
    """Get items for a consignment. consignment_id can be either an integer ID or a consignment number."""
    cid = _get_consignment_id(consignment_id)
    if cid is None:
        raise HTTPException(status_code=404, detail=f"Consignment not found: {consignment_id}")
    
    q = """
    SELECT id, serial_number, position_idx, grade1, grade2, country,
           year_and_name, addl1, addl2, addl3, label_type
    FROM consignment_items
    WHERE consignment_id=%s
    ORDER BY position_idx
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(q, (cid,))
        return cur.fetchall()

@router.get("/{consignment_id}/export.csv", response_class=PlainTextResponse)
def export_csv(consignment_id: str, user=Depends(require_user)):
    cid = _get_consignment_id(consignment_id)
    if cid is None:
        raise HTTPException(status_code=404, detail=f"Consignment not found: {consignment_id}")
    header = [
        "Serial Number","Grade 1","Grade 2","Country",
        "Year and Name","Additional Information",
        "Additional Information 2","Additional Information 3","Label Type"
    ]
    q = """
    SELECT
      (c.number || '-' || lpad(ci.position_idx::text, 3, '0')) AS serial,
      ci.grade1, ci.grade2, ci.country, ci.year_and_name,
      ci.addl1, ci.addl2, ci.addl3, ci.label_type
    FROM consignment_items ci
    JOIN consignments c ON c.id = ci.consignment_id
    WHERE ci.consignment_id = %s
    ORDER BY ci.position_idx
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(q, (cid,))
        rows = cur.fetchall()
    out = [",".join(header)]
    for r in rows:
        out.append(",".join("" if v is None else str(v) for v in r))
    return "\n".join(out)

@router.post("/{consignment_id}/sync-to-certs")
def sync_to_certs(consignment_id: str, user=Depends(require_user)):
    cid = _get_consignment_id(consignment_id)
    if cid is None:
        raise HTTPException(status_code=404, detail=f"Consignment not found: {consignment_id}")
    cons = _fetch_consignment(cid)
    if not cons:
        raise HTTPException(status_code=404, detail="consignment not found")

    fetch_items = """
    SELECT serial_number, country, year_and_name, addl1, addl2, addl3, grade1, grade2
    FROM consignment_items
    WHERE consignment_id=%s
    ORDER BY position_idx
    """
    upsert = """
    INSERT INTO certs (
      serial_number, country, year, coin_name,
      addl1, addl2, addl3,
      grade1, grade2,
      coin_key, grade_family, grade_number
    )
    VALUES
      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (serial_number) DO UPDATE SET
      country=EXCLUDED.country,
      year=EXCLUDED.year,
      coin_name=EXCLUDED.coin_name,
      addl1=EXCLUDED.addl1,
      addl2=EXCLUDED.addl2,
      addl3=EXCLUDED.addl3,
      grade1=EXCLUDED.grade1,
      grade2=EXCLUDED.grade2,
      coin_key=EXCLUDED.coin_key,
      grade_family=EXCLUDED.grade_family,
      grade_number=EXCLUDED.grade_number
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(fetch_items, (cid,))
        items = cur.fetchall()

    count = 0
    with pool.connection() as conn, conn.cursor() as cur:
        for it in items:
            year, coin_name = _split_year_and_name(it["year_and_name"])
            fam, num = _extract_family_and_number(it["grade1"])
            coin_key = _make_coin_key(it["country"], year, coin_name, it["addl1"])
            cur.execute(upsert, (
                it["serial_number"], it["country"], year, coin_name,
                it["addl1"], it["addl2"], it["addl3"],
                it["grade1"], it["grade2"],
                coin_key, fam, num
            ))
            count += 1
    return {"ok": True, "consignment_id": consignment_id, "synced": count}
