# ~/sangs-agent/admin_ingest.py
import os
import csv
import io
import json
from typing import Dict, List, Optional

import httpx
from psycopg.rows import dict_row
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from fastapi.responses import JSONResponse

from db import pool  # use the shared pool
from auth import require_user

router = APIRouter(prefix="/admin/ingest", tags=["admin-ingest"])

LABELS_SHEETS_CSV_URL = os.getenv("LABELS_SHEETS_CSV_URL")
CERTS_SHEETS_CSV_URL  = os.getenv("CERTS_SHEETS_CSV_URL")

def _hdr(s: str) -> str:
    """Normalize CSV header: strip BOM, lowercase, convert spaces to underscores."""
    if not s:
        return ""
    # Remove BOM character if present
    s = s.lstrip('\ufeff')
    # Strip whitespace, lowercase, and replace spaces with underscores
    return s.strip().lower().replace(" ", "_")

def _present(x: Optional[str]) -> bool:
    return bool(x and str(x).strip())

def _compute_grade_sort_key(grade1: Optional[str], grade2: Optional[str]) -> int:
    def base_for_grade(g: Optional[str]) -> int:
        if not g:
            return 0
        g = g.strip().upper()
        if "DETAIL" in g:
            if g.startswith("AU"): return 50
            if g.startswith("XF"): return 40
            if g.startswith("VF"): return 30
            if g == "F" or g.startswith("F "): return 25
            if g.startswith("VG"): return 20
            if g == "G" or g.startswith("G "): return 15
            if g.startswith("AG"): return 10
            if g.startswith("FR"): return 5
            if g.startswith("PO"): return 1
            return 0
        if g.startswith(("PF", "MS", "PL", "UNC")):
            digits = "".join(ch for ch in g if ch.isdigit())
            if digits:
                try:
                    n = int(digits)
                    if 1 <= n <= 70:
                        return n
                except ValueError:
                    pass
            return 0
        if g.startswith("AU"): return 50
        if g.startswith("XF"): return 40
        if g.startswith("VF"): return 30
        if g == "F": return 25
        if g.startswith("VG"): return 20
        if g == "G": return 15
        if g.startswith("AG"): return 10
        if g.startswith("FR"): return 5
        if g.startswith("PO"): return 1
        return 0
    base = base_for_grade(grade1)
    bump = 0
    if _present(grade2):
        g2 = grade2.strip().upper()
        if g2 in ("UCAM", "DEEP CAMEO"): bump = 2
        elif g2 == "CAM": bump = 1
        elif g2 in ("PL", "PROOFLIKE"): bump = 1
    return base * 10 + bump

async def _fetch_csv_rows(url: str) -> List[Dict[str, str]]:
    if not url:
        raise HTTPException(status_code=400, detail="CSV url not provided")
    headers = {"User-Agent": "sangs-agent/1.0 (+https://sangs.co.za)"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0, headers=headers) as client:
        r = await client.get(url)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"CSV fetch failed: {r.status_code}")
        text = r.content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: List[Dict[str, str]] = []
    for raw in reader:
        rows.append({ _hdr(k): (v or "").strip() for k, v in raw.items() })
    return rows

async def _ingest_labels_from_rows(rows: List[Dict[str, str]]) -> int:
    if not rows:
        return 0
    inserted = 0
    with pool.connection() as conn, conn.cursor() as cur:
        for r in rows:
            id_    = r.get("id") or ""
            if not id_: continue
            country = r.get("country") or None
            year    = r.get("year") or None
            coin    = r.get("coin_name") or None
            addl1   = r.get("addl1") or None
            addl2   = r.get("addl2") or None
            addl3   = r.get("addl3") or None
            grade_label   = r.get("grade_label") or None
            serial_format = r.get("serial_format") or None
            aliases_arr: Optional[List[str]] = None
            aliases_raw = r.get("aliases") or ""
            if aliases_raw:
                if aliases_raw.strip().startswith("["):
                    try:
                        aliases_arr = [str(x) for x in json.loads(aliases_raw)]
                    except Exception:
                        aliases_arr = None
                else:
                    parts = [p.strip() for p in aliases_raw.split(",") if p.strip()]
                    aliases_arr = parts or None
            cur.execute(
                """
                INSERT INTO label_kb
                  (id, country, year, coin_name, addl1, addl2, addl3,
                   grade_label, serial_format, aliases, key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                  country       = EXCLUDED.country,
                  year          = EXCLUDED.year,
                  coin_name     = EXCLUDED.coin_name,
                  addl1         = EXCLUDED.addl1,
                  addl2         = EXCLUDED.addl2,
                  addl3         = EXCLUDED.addl3,
                  grade_label   = EXCLUDED.grade_label,
                  serial_format = EXCLUDED.serial_format,
                  aliases       = EXCLUDED.aliases,
                  key           = EXCLUDED.key
                """,
                (id_, country, year, coin, addl1, addl2, addl3,
                 grade_label, serial_format, aliases_arr, id_,),
            )
            inserted += 1
    return inserted

@router.post("/labels-from-sheets")
async def labels_from_sheets(csv_url: Optional[str] = Query(default=None), user=Depends(require_user)):
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="forbidden")
    url = csv_url or LABELS_SHEETS_CSV_URL
    rows = await _fetch_csv_rows(url)
    n = await _ingest_labels_from_rows(rows)
    return JSONResponse({"ok": True, "upserted": n})

async def _ingest_certs_from_rows(rows: List[Dict[str, str]], skip_duplicates: bool = True) -> Dict[str, int]:
    """Ingest certs from CSV rows. Returns dict with inserted, updated, skipped counts.
    Optimized for large CSV files with hundreds of rows."""
    if not rows:
        return {"inserted": 0, "updated": 0, "skipped": 0}
    
    inserted = 0
    updated = 0
    skipped = 0
    duplicate_count = 0
    
    # Filter out rows without serial numbers
    # Try multiple column name variations
    valid_rows = []
    missing_serial_count = 0
    sample_serial_values = []
    for idx, r in enumerate(rows):
        # Try various column name formats
        serial_raw = (r.get("serial_number") or 
                     r.get("serial") or 
                     r.get("serialnumber") or  # no space/underscore
                     "")
        serial = serial_raw.strip() if serial_raw else ""
        
        # Collect sample values for debugging (first 3 rows)
        if idx < 3:
            sample_serial_values.append(f"Row {idx+1}: '{serial_raw}' -> '{serial}'")
        
        if not serial:
            skipped += 1
            missing_serial_count += 1
            continue
        valid_rows.append((serial, r))
    
    if not valid_rows:
        # Log column names for debugging
        if rows:
            sample_row = rows[0]
            available_cols = list(sample_row.keys())
            print(f"[ingest] No valid rows found. Available CSV columns: {available_cols}")
            print(f"[ingest] Missing serial_number: {missing_serial_count} rows, Total rows: {len(rows)}")
            if sample_serial_values:
                print(f"[ingest] Sample serial_number values: {sample_serial_values}")
                # Show actual raw value from first row
                first_serial_raw = sample_row.get("serial_number", "NOT_FOUND")
                print(f"[ingest] First row serial_number raw value: {repr(first_serial_raw)}")
        return {
            "inserted": 0, 
            "updated": 0, 
            "skipped": skipped, 
            "skip_reason": "missing_serial" if missing_serial_count == skipped else "unknown",
            "debug": {
                "available_columns": list(rows[0].keys()) if rows else [],
                "sample_serial_values": sample_serial_values[:5],
                "missing_serial_count": missing_serial_count
            }
        }
    
    with pool.connection() as conn, conn.cursor() as cur:
        # Batch check for existing serials (much faster for large uploads)
        existing_serials = set()
        if skip_duplicates:
            serials_to_check = [serial for serial, _ in valid_rows]
            # Check in batches of 1000 to avoid query size limits
            batch_size = 1000
            for i in range(0, len(serials_to_check), batch_size):
                batch = serials_to_check[i:i + batch_size]
                placeholders = ",".join(["%s"] * len(batch))
                check_sql = f"SELECT serial_number FROM certs WHERE serial_number IN ({placeholders})"
                cur.execute(check_sql, batch)
                existing_serials.update(row[0] for row in cur.fetchall())
        
        # Process all rows
        for serial, r in valid_rows:
            exists = serial in existing_serials
            
            # Skip if duplicate and skip_duplicates is True
            if skip_duplicates and exists:
                skipped += 1
                duplicate_count += 1
                continue
            
            # Map columns with flexible naming (after _hdr normalization, spaces become underscores)
            country = r.get("country") or None
            year    = r.get("year") or None
            coin    = (r.get("coin_name") or 
                      r.get("coinname") or
                      r.get("year_and_name") or  # sometimes year and name is in one column
                      None)
            addl1   = (r.get("addl1") or 
                      r.get("additional_information") or  # first additional info column
                      None)
            addl2   = (r.get("addl2") or 
                      r.get("additional_information_2") or
                      None)
            addl3   = (r.get("addl3") or 
                      r.get("additional_information_3") or
                      None)
            grade1  = (r.get("grade1") or 
                     r.get("grade_1") or
                     None)
            grade2  = (r.get("grade2") or 
                     r.get("grade_2") or
                     None)
            
            # If year_and_name exists but coin_name doesn't, try to extract
            if not coin and r.get("year_and_name"):
                year_and_name = r.get("year_and_name", "").strip()
                # Try to split year and name (year is usually first 4 digits)
                if year_and_name:
                    # If year is separate, use year_and_name as coin_name
                    if not year:
                        # Try to extract year from year_and_name
                        import re
                        year_match = re.match(r'^(\d{4})\s+(.+)', year_and_name)
                        if year_match:
                            year = year_match.group(1)
                            coin = year_match.group(2).strip()
                        else:
                            coin = year_and_name
                    else:
                        coin = year_and_name
            gkey = _compute_grade_sort_key(grade1, grade2)
            ckey = "|".join([x or "" for x in (country, year, coin, addl1)])
            
            is_update = exists
            
            cur.execute(
                """
                INSERT INTO certs
                  (serial_number, country, year, coin_name, addl1, addl2, addl3,
                   grade1, grade2, coin_key, grade_family, grade_number)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (serial_number) DO UPDATE SET
                  country        = EXCLUDED.country,
                  year           = EXCLUDED.year,
                  coin_name      = EXCLUDED.coin_name,
                  addl1          = EXCLUDED.addl1,
                  addl2          = EXCLUDED.addl2,
                  addl3          = EXCLUDED.addl3,
                  grade1         = EXCLUDED.grade1,
                  grade2         = EXCLUDED.grade2,
                  coin_key       = EXCLUDED.coin_key,
                  grade_family   = EXCLUDED.grade_family,
                  grade_number   = EXCLUDED.grade_number
                """,
                (serial, country, year, coin, addl1, addl2, addl3, grade1, grade2, ckey, None, None),
            )
            if is_update:
                updated += 1
            else:
                inserted += 1
    
    # Determine skip reason if all were skipped
    skip_reason = None
    if skipped > 0 and inserted == 0 and updated == 0:
        if duplicate_count == skipped:
            skip_reason = "duplicates"
        elif missing_serial_count == skipped:
            skip_reason = "missing_serial"
    
    result = {"inserted": inserted, "updated": updated, "skipped": skipped}
    if skip_reason:
        result["skip_reason"] = skip_reason
    return result

@router.post("/certs-from-sheets")
async def certs_from_sheets(csv_url: Optional[str] = Query(default=None), user=Depends(require_user)):
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="forbidden")
    url = csv_url or CERTS_SHEETS_CSV_URL
    rows = await _fetch_csv_rows(url)
    result = await _ingest_certs_from_rows(rows, skip_duplicates=True)
    return JSONResponse({"ok": True, **result})

@router.post("/certs-from-file")
async def certs_from_file(
    file: UploadFile = File(...),
    skip_duplicates: bool = Query(default=True),
    user=Depends(require_user)
):
    """Upload a CSV file directly to import certs into the database.
    Duplicate serial numbers will be skipped if skip_duplicates=True."""
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="forbidden")
    
    if not file.filename or not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    
    # Read the uploaded file
    contents = await file.read()
    text = contents.decode("utf-8", errors="replace")
    
    # Parse CSV - try semicolon delimiter first (common in Excel exports), then comma
    # Detect delimiter by checking first line
    first_line = text.split('\n')[0] if text else ""
    delimiter = ';' if ';' in first_line and first_line.count(';') > first_line.count(',') else ','
    
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows: List[Dict[str, str]] = []
    for raw in reader:
        rows.append({ _hdr(k): (v or "").strip() for k, v in raw.items() })
    
    if not rows:
        raise HTTPException(status_code=400, detail="CSV file is empty or has no valid rows")
    
    # Log CSV column names for debugging
    if rows:
        sample_cols = list(rows[0].keys())
        print(f"[ingest] CSV columns detected: {sample_cols}")
        # Check if serial_number exists
        if "serial_number" not in sample_cols and "serial" not in sample_cols:
            print(f"[ingest] WARNING: No 'serial_number' or 'serial' column found!")
    
    # Ingest the rows
    result = await _ingest_certs_from_rows(rows, skip_duplicates=skip_duplicates)
    
    # Log for debugging
    print(f"[ingest] File {file.filename}: {result['inserted']} inserted, {result['updated']} updated, {result['skipped']} skipped")
    
    # Include debug info in response if available
    response_data = {"ok": True, **result, "filename": file.filename}
    if "debug" in result:
        response_data["debug"] = result["debug"]
    
    return JSONResponse(response_data)

@router.post("/run-now")
async def run_now(user=Depends(require_user)):
    if user.get("role") not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="forbidden")
    out = {"labels": 0, "certs": 0}
    if LABELS_SHEETS_CSV_URL:
        rows = await _fetch_csv_rows(LABELS_SHEETS_CSV_URL)
        out["labels"] = await _ingest_labels_from_rows(rows)
    if CERTS_SHEETS_CSV_URL:
        rows = await _fetch_csv_rows(CERTS_SHEETS_CSV_URL)
        out["certs"] = await _ingest_certs_from_rows(rows)
    return JSONResponse({"ok": True, **out})
