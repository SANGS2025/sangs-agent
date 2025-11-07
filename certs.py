# ~/sangs-agent/certs.py
import os
from typing import Optional

import psycopg
from psycopg_pool import ConnectionPool
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

# --- DB pool (shared) ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL")
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=5, kwargs={"autocommit": True})

router = APIRouter(prefix="/certs", tags=["certs"])

# --- auth dependency ---
from auth import require_user  # {"email":..., "role":...}

# ---------- helpers ----------
_FAMILY_ALIASES = {"UNC": "MS"}

def _norm_family(token: str) -> str:
    t = (token or "").upper().strip()
    return _FAMILY_ALIASES.get(t, t) if t else "UNK"

def _extract_family_and_number(grade1: Optional[str]) -> tuple[str, Optional[int]]:
    g1 = (grade1 or "").upper().replace("UNC", "MS").strip()
    if not g1:
        return ("UNK", None)
    letters = "".join(ch for ch in g1 if ch.isalpha())
    digits  = "".join(ch for ch in g1 if ch.isdigit())
    fam = _norm_family(letters or "UNK")
    num: Optional[int] = None
    if digits:
        try:
            num = int(digits)
        except ValueError:
            num = None
    return (fam, num)

def _make_coin_key(country: Optional[str], year: Optional[str],
                   coin_name: Optional[str], addl1: Optional[str]) -> str:
    nz = lambda v: (v or "").strip()
    return "|".join([nz(country), nz(year), nz(coin_name), nz(addl1)])

# ---------- models ----------
class CertUpsertIn(BaseModel):
    serial_number: str = Field(min_length=3)
    country: Optional[str] = None
    year: Optional[str] = None
    coin_name: Optional[str] = None
    addl1: Optional[str] = None
    addl2: Optional[str] = None
    addl3: Optional[str] = None
    grade1: Optional[str] = None
    grade2: Optional[str] = None
    is_details: Optional[bool] = False
    details_reason: Optional[str] = None

class CertOut(BaseModel):
    id: int
    serial_number: str
    country: Optional[str] = None
    year: Optional[str] = None
    coin_name: Optional[str] = None
    addl1: Optional[str] = None
    addl2: Optional[str] = None
    addl3: Optional[str] = None
    grade1: Optional[str] = None
    grade2: Optional[str] = None
    coin_key: Optional[str] = None
    grade_family: Optional[str] = None
    grade_number: Optional[int] = None
    is_details: Optional[bool] = None
    details_reason: Optional[str] = None

# ---------- SEARCH FIRST (avoid clash with "/{serial}") ----------
@router.get("/lookup")
@router.get("/search")
def lookup_certs(
    q: str = Query(..., min_length=1),
    limit: int = Query(200, ge=1, le=1000),
    user=Depends(require_user),
):
    # restrict staff/admin
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="forbidden")

    like = f"%{q.strip()}%"
    sql = """
    SELECT
      COALESCE(serial_number,'') AS serial_number,
      COALESCE(grade1,'')        AS grade1,
      COALESCE(grade2,'')        AS grade2,
      COALESCE(country,'')       AS country,
      TRIM(BOTH ' ' FROM COALESCE(year,'') || ' ' || COALESCE(coin_name,'')) AS year_and_name,
      COALESCE(addl1,'')         AS addl1,
      COALESCE(addl2,'')         AS addl2,
      COALESCE(addl3,'')         AS addl3
    FROM certs
    WHERE
         country ILIKE %s
      OR (COALESCE(year,'') || ' ' || COALESCE(coin_name,'')) ILIKE %s
      OR addl1 ILIKE %s
      OR addl2 ILIKE %s
      OR addl3 ILIKE %s
      OR coin_name ILIKE %s
    ORDER BY country, year, coin_name, addl1, serial_number
    LIMIT %s
    """
    params = (like, like, like, like, like, like, limit)

    out = []
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        for r in cur.fetchall():
            out.append({
                "serial_number": r[0],
                "grade1":        r[1],
                "grade2":        r[2],
                "country":       r[3],
                "year_and_name": r[4],
                "addl1":         r[5],
                "addl2":         r[6],
                "addl3":         r[7],
            })
    return out

# ---------- CRUD ----------
@router.post("/upsert", response_model=CertOut)
def upsert_cert(payload: CertUpsertIn, user=Depends(require_user)):
    ck = _make_coin_key(payload.country, payload.year, payload.coin_name, payload.addl1)
    fam, num = _extract_family_and_number(payload.grade1)

    sql = """
    INSERT INTO certs (
      serial_number, country, year, coin_name,
      addl1, addl2, addl3,
      grade1, grade2,
      coin_key, grade_family, grade_number, is_details, details_reason
    )
    VALUES
      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
      grade_number=EXCLUDED.grade_number,
      is_details=EXCLUDED.is_details,
      details_reason=EXCLUDED.details_reason
    RETURNING
      id, serial_number, country, year, coin_name, addl1, addl2, addl3,
      grade1, grade2, coin_key, grade_family, grade_number, is_details, details_reason
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (
            payload.serial_number,
            payload.country, payload.year, payload.coin_name,
            payload.addl1, payload.addl2, payload.addl3,
            payload.grade1, payload.grade2,
            ck, fam, num, bool(payload.is_details), payload.details_reason
        ))
        return cur.fetchone()

@router.get("/list")
def list_certs(
    q: Optional[str] = Query(default=None),
    limit: int = Query(100, ge=1, le=50000),  # Increased limit for large databases
    offset: int = Query(0, ge=0),
    user=Depends(require_user)
):
    """List all certs with optional search. Returns paginated results."""
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="forbidden")
    
    if q:
        like = f"%{q.strip()}%"
        sql = """
        SELECT id, serial_number, country, year, coin_name, addl1, addl2, addl3,
               grade1, grade2, coin_key, grade_family, grade_number, is_details, details_reason
        FROM certs
        WHERE
          serial_number ILIKE %s
          OR country ILIKE %s
          OR (COALESCE(year,'') || ' ' || COALESCE(coin_name,'')) ILIKE %s
          OR addl1 ILIKE %s
          OR addl2 ILIKE %s
          OR addl3 ILIKE %s
          OR coin_name ILIKE %s
        ORDER BY id DESC
        LIMIT %s OFFSET %s
        """
        count_sql = """
        SELECT COUNT(*) AS count
        FROM certs
        WHERE
          serial_number ILIKE %s
          OR country ILIKE %s
          OR (COALESCE(year,'') || ' ' || COALESCE(coin_name,'')) ILIKE %s
          OR addl1 ILIKE %s
          OR addl2 ILIKE %s
          OR addl3 ILIKE %s
          OR coin_name ILIKE %s
        """
        params = (like, like, like, like, like, like, like, limit, offset)
        count_params = (like, like, like, like, like, like, like)
    else:
        sql = """
        SELECT id, serial_number, country, year, coin_name, addl1, addl2, addl3,
               grade1, grade2, coin_key, grade_family, grade_number, is_details, details_reason
        FROM certs
        ORDER BY id DESC
        LIMIT %s OFFSET %s
        """
        count_sql = "SELECT COUNT(*) AS count FROM certs"
        params = (limit, offset)
        count_params = ()
    
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        items = cur.fetchall()
        cur.execute(count_sql, count_params)
        total_row = cur.fetchone()
        total = total_row["count"] if total_row else 0
    
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset
    }

@router.delete("/all")
def delete_all_certs(
    auth_code: str = Query(..., description="Authentication code required to clear database"),
    user=Depends(require_user)
):
    """Delete all certs from the database. Admin only. Requires authentication code."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="forbidden - admin only")
    
    # Verify authentication code
    import os
    required_code = os.getenv("DB_CLEAR_AUTH_CODE", "")
    if not required_code:
        raise HTTPException(status_code=500, detail="Database clear authentication not configured")
    
    if auth_code != required_code:
        raise HTTPException(status_code=403, detail="Invalid authentication code")
    
    sql = "DELETE FROM certs RETURNING id"
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        deleted = cur.rowcount
    return {"ok": True, "deleted_count": deleted}

@router.get("/{serial}", response_model=CertOut)
def get_cert(serial: str, user=Depends(require_user)):
    sql = """
    SELECT id, serial_number, country, year, coin_name, addl1, addl2, addl3,
           grade1, grade2, coin_key, grade_family, grade_number, is_details, details_reason
    FROM certs
    WHERE serial_number=%s
    LIMIT 1
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (serial,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="cert not found")
        return row

@router.delete("/{serial}")
def delete_cert(serial: str, user=Depends(require_user)):
    """Delete a cert by serial number."""
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="forbidden")
    
    sql = "DELETE FROM certs WHERE serial_number=%s RETURNING id"
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (serial,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="cert not found")
        return {"ok": True, "deleted": serial}

@router.get("/{serial}/rank")
def rank_cert(serial: str, user=Depends(require_user)):
    fetch = """
    SELECT serial_number, country, year, coin_name, addl1,
           grade1, grade_family, grade_number, coin_key
    FROM certs
    WHERE serial_number=%s
    LIMIT 1
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(fetch, (serial,))
        cert = cur.fetchone()
        if not cert:
            raise HTTPException(status_code=404, detail="cert not found")

    fam = cert.get("grade_family")
    num = cert.get("grade_number")
    if not fam or num is None:
        return {
            "serial_number": serial,
            "message": "Not enough data to rank this coin yet.",
            "same_grade_others": 0,
            "higher_count": 0
        }

    sql = "SELECT grade_number FROM certs WHERE coin_key=%s AND grade_family=%s"
    same_grade_others = 0
    higher = 0
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (cert["coin_key"], fam))
        for (n2,) in cur.fetchall():
            if n2 is None:
                continue
            if n2 == num:
                same_grade_others += 1
            elif n2 > num:
                higher += 1
    if same_grade_others > 0:
        same_grade_others -= 1

    coin_label = f"{(cert.get('year') or '').strip()} {(cert.get('coin_name') or '').strip()}".strip()
    grade_label = (cert.get("grade1") or "").strip()
    disp = f"{coin_label} - {grade_label}" if coin_label and grade_label else cert.get("serial_number")

    msg = (f"{disp} - Finest Known at SANGS with 0 other coins in this Grade"
           if higher == 0 and same_grade_others == 0
           else f"{disp} - {same_grade_others} other Coins also in this grade and {higher} graded higher at SANGS")

    return {
        "serial_number": serial,
        "message": msg,
        "same_grade_others": same_grade_others,
        "higher_count": higher
    }
