# ~/sangs-agent/labels.py
import os
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Depends, Body
from psycopg.rows import dict_row

from auth import require_user
from db import pool  # use the shared pool

router = APIRouter(prefix="/labels", tags=["labels"])

# Optional webhook for Sheets push (approval audit / external log)
APPROVAL_WEBHOOK_URL = os.getenv("APPROVAL_WEBHOOK_URL")

# -----------------------------
# LOOKUP (from certs table)
# -----------------------------
@router.get("/lookup")
def label_lookup(q: str, limit: int = 200, user=Depends(require_user)):
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="forbidden")

    like = f"%{q.strip()}%"
    sql = """
    SELECT
      serial_number,
      COALESCE(grade1,'') AS grade1,
      COALESCE(grade2,'') AS grade2,
      COALESCE(country,'') AS country,
      TRIM(BOTH ' ' FROM COALESCE(year,'') || ' ' || COALESCE(coin_name,'')) AS year_and_name,
      COALESCE(addl1,'') AS addl1,
      COALESCE(addl2,'') AS addl2,
      COALESCE(addl3,'') AS addl3
    FROM certs
    WHERE
      country ILIKE %s
      OR (COALESCE(year,'') || ' ' || COALESCE(coin_name,'')) ILIKE %s
      OR addl1 ILIKE %s
      OR addl2 ILIKE %s
      OR addl3 ILIKE %s
      OR coin_name ILIKE %s
    ORDER BY id DESC
    LIMIT %s
    """
    params = (like, like, like, like, like, like, limit)

    rows: List[Dict[str, Any]] = []
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for r in cur.fetchall():
            rows.append({
                "serial_number": r[0],
                "grade1": r[1],
                "grade2": r[2],
                "country": r[3],
                "year_and_name": r[4],
                "addl1": r[5],
                "addl2": r[6],
                "addl3": r[7],
            })
    return rows

# -----------------------------
# APPROVE (assign to consignment)
# -----------------------------
def _next_position_idx(consignment_id: int) -> int:
    sql = "SELECT COALESCE(MAX(position_idx), 0) FROM consignment_items WHERE consignment_id=%s"
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (consignment_id,))
        (mx,) = cur.fetchone()
        return int(mx) + 1

def _fetch_consignment_by_number(cons_no: str) -> Optional[Dict[str, Any]]:
    sql = "SELECT id, number FROM consignments WHERE number=%s"
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (cons_no,))
        return cur.fetchone()

def _split_year_and_name(s: Optional[str]):
    s = (s or "").strip()
    if not s:
        return (None, None)
    parts = s.split()
    if parts and parts[0].isdigit():
        return (parts[0], " ".join(parts[1:]).strip() or None)
    return (None, s)

@router.post("/approve")
def approve_labels(
    body: Dict[str, Any] = Body(...),
    user=Depends(require_user)
):
    """
    Body:
    {
      "consignment_number": "12438485",
      "items": [
        {
          "country": "ZAR",
          "year_and_name": "1892 1 Pond",
          "addl1": "Double Shaft",
          "addl2": "Gold",
          "addl3": "",
          "grade1": "MS62",
          "grade2": ""
        },
        ...
      ]
    }

    Effect:
      - Finds consignment by number
      - Appends items to consignment_items, assigning serials <cons>-001, -002,... in order
      - Returns list of inserted rows
      - If APPROVAL_WEBHOOK_URL is set, sends a JSON POST for external logging (best-effort)
    """
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="forbidden")

    cons_no = (body.get("consignment_number") or "").strip()
    items: List[Dict[str, Any]] = body.get("items") or []

    if not cons_no:
        raise HTTPException(status_code=400, detail="consignment_number required")
    if not items:
        raise HTTPException(status_code=400, detail="items required")

    cons = _fetch_consignment_by_number(cons_no)
    if not cons:
        raise HTTPException(status_code=404, detail="consignment not found")

    inserted: List[Dict[str, Any]] = []
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        for it in items:
            idx = _next_position_idx(cons["id"])
            serial = f"{cons['number']}-{idx:03d}"

            # year & coin_name are not required for consignment_items; they live in certs
            # here we keep your consignment_items schema as is:
            cur.execute(
                """
                INSERT INTO consignment_items (
                  consignment_id, position_idx, serial_number,
                  grade1, grade2, country, year_and_name,
                  addl1, addl2, addl3, label_type
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id, serial_number, position_idx, grade1, grade2, country, year_and_name, addl1, addl2, addl3, label_type
                """,
                (
                    cons["id"], idx, serial,
                    (it.get("grade1") or None),
                    (it.get("grade2") or None),
                    (it.get("country") or None),
                    (it.get("year_and_name") or None),
                    (it.get("addl1") or None),
                    (it.get("addl2") or None),
                    (it.get("addl3") or None),
                    None,  # label_type will be recomputed later if needed
                ),
            )
            inserted.append(cur.fetchone())

    # Best-effort webhook (optional)
    if APPROVAL_WEBHOOK_URL:
        try:
            import httpx
            payload = {
                "consignment_number": cons_no,
                "approved_by": user.get("email"),
                "count": len(inserted),
                "items": inserted,
            }
            with httpx.Client(timeout=5.0) as client:
                client.post(APPROVAL_WEBHOOK_URL, json=payload)
        except Exception:
            # Silent fail – we don't want UI errors if webhook is down
            pass

    return {"ok": True, "consignment_number": cons_no, "inserted": inserted}
