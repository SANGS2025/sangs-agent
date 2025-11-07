# ~/sangs-agent/public_certs.py
# Public (read-only) certificate verification API
import os
import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row
from db import pool

router = APIRouter(prefix="/api/certs", tags=["public-certs"])

# Allowed display_number patterns
LEGACY_PATTERN = re.compile(r'^20\d{2}-\d{4}-\d{3}$')  # e.g., 2025-1200-003
EIGHT_DIGIT_PATTERN = re.compile(r'^\d{9}-\d{3}$')  # e.g., 83420175-001

def validate_display_number(display_number: str) -> bool:
    """Validate display_number against allowed patterns."""
    return bool(LEGACY_PATTERN.match(display_number) or EIGHT_DIGIT_PATTERN.match(display_number))

@router.get("/{display_number}")
def get_cert_by_display_number(display_number: str):
    """
    Public endpoint to lookup a certificate by display_number or canonical_id.
    Returns 404 if not found or not verified.
    """
    # Check if it's a canonical_id (UUID or numeric) or display_number
    is_canonical_id = False
    if display_number.isdigit() or (len(display_number) > 10 and '-' not in display_number):
        # Likely a canonical_id (numeric ID or UUID)
        is_canonical_id = True
    else:
        # Validate display_number format
        if not validate_display_number(display_number):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid certificate number format. Expected: 2025-1200-003 or 83420175-001"
            )
    
    # Build SQL query based on whether it's a canonical_id or display_number
    if is_canonical_id:
        # Lookup by canonical_id (numeric ID)
        sql = """
        SELECT 
          c.id::text as canonical_id,
          c.display_number,
          c.status,
          c.updated_at,
          c.verified_at,
          c.superseded_by,
          co.country,
          co.denomination,
          co.denomination_slug,
          co.year,
          co.variety,
          co.metal,
          co.strike,
          co.grade_text,
          co.grade_num,
          co.label_type,
          co.pedigree,
          co.notes,
          (SELECT path FROM images WHERE cert_id = c.id AND type = 'obv' LIMIT 1) as obv_path,
          (SELECT path FROM images WHERE cert_id = c.id AND type = 'rev' LIMIT 1) as rev_path
        FROM certs c
        LEFT JOIN coins co ON co.cert_id = c.id
        WHERE c.id::text = %s
        AND c.status IN ('verified', 'reslabbed')
        LIMIT 1
        """
        query_param = display_number
    else:
        # Lookup by display_number
        sql = """
        SELECT 
          c.id::text as canonical_id,
          c.display_number,
          c.status,
          c.updated_at,
          c.verified_at,
          c.superseded_by,
          co.country,
          co.denomination,
          co.denomination_slug,
          co.year,
          co.variety,
          co.metal,
          co.strike,
          co.grade_text,
          co.grade_num,
          co.label_type,
          co.pedigree,
          co.notes,
          (SELECT path FROM images WHERE cert_id = c.id AND type = 'obv' LIMIT 1) as obv_path,
          (SELECT path FROM images WHERE cert_id = c.id AND type = 'rev' LIMIT 1) as rev_path
        FROM certs c
        LEFT JOIN coins co ON co.cert_id = c.id
        WHERE c.display_number = %s
        AND c.status IN ('verified', 'reslabbed')
        LIMIT 1
        """
        query_param = display_number
    
    try:
        with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (query_param,))
            row = cur.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="Certificate not found")
            
            # Check if coin data exists - if not, return 404
            if not row.get("denomination"):
                raise HTTPException(
                    status_code=404, 
                    detail="Certificate found but coin data is not available. This certificate may not be fully processed yet."
                )
            
            # Handle reslabbed status
            if row.get("status") == "reslabbed" and row.get("superseded_by"):
                # Get the new cert
                new_sql = """
                SELECT display_number
                FROM certs
                WHERE id = %s
                LIMIT 1
                """
                cur.execute(new_sql, (row["superseded_by"],))
                new_cert = cur.fetchone()
                if new_cert:
                    raise HTTPException(
                        status_code=410,
                        detail="This certificate has been reslabbed",
                        headers={"Location": f"/cert/{new_cert['display_number']}"}
                    )
            
            # Build response - handle None values safely
            verified_at = row.get("verified_at")
            superseded_by = row.get("superseded_by")
            
            result = {
                "canonical_id": row.get("canonical_id") or "",
                "display_number": row.get("display_number"),
                "status": row.get("status"),
                "coin": {
                    "country": row.get("country"),
                    "denomination": row.get("denomination"),
                    "denomination_slug": row.get("denomination_slug"),
                    "year": row.get("year"),
                    "variety": row.get("variety"),
                    "metal": row.get("metal"),
                    "strike": row.get("strike"),
                    "grade_text": row.get("grade_text"),
                    "grade_num": row.get("grade_num"),
                    "label_type": row.get("label_type"),
                    "pedigree": row.get("pedigree"),
                    "notes": row.get("notes"),
                },
                "images": {
                    "obv": row.get("obv_path") if row.get("obv_path") else None,
                    "rev": row.get("rev_path") if row.get("rev_path") else None,
                },
                "verified_at": verified_at.isoformat() if verified_at else None,
                "superseded_by": str(superseded_by) if superseded_by else None,
            }
            
            return result
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{display_number}/population")
def get_cert_population(display_number: str):
    """
    Get population statistics for a certificate.
    Returns counts for same grade and higher grades.
    """
    # Check if it's a canonical_id or display_number
    is_canonical_id = False
    if display_number.isdigit() or (len(display_number) > 10 and '-' not in display_number):
        is_canonical_id = True
    else:
        if not validate_display_number(display_number):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid certificate number format"
            )
    
    # First get the cert and coin data
    if is_canonical_id:
        cert_sql = """
        SELECT c.id, co.denomination_slug, co.strike, co.year, co.grade_num
        FROM certs c
        JOIN coins co ON co.cert_id = c.id
        WHERE c.id::text = %s
        AND c.status IN ('verified', 'pending', 'reslabbed')
        LIMIT 1
        """
    else:
        cert_sql = """
        SELECT c.id, co.denomination_slug, co.strike, co.year, co.grade_num
        FROM certs c
        JOIN coins co ON co.cert_id = c.id
        WHERE c.display_number = %s
        AND c.status IN ('verified', 'pending', 'reslabbed')
        LIMIT 1
        """
    
    try:
        with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(cert_sql, (display_number,))
            cert = cur.fetchone()
            
            if not cert:
                raise HTTPException(status_code=404, detail="Certificate not found")
            
            denomination_slug = cert.get("denomination_slug")
            strike = cert.get("strike")
            year = cert.get("year")
            grade_num = cert.get("grade_num")
            
            if not all([denomination_slug, strike, year, grade_num]):
                return {
                    "same_grade_count": 0,
                    "higher_grade_count": 0,
                    "total_in_denomination": 0,
                    "message": "Population data not available for this certificate"
                }
            
            # Get population data from precomputed table (optimized single query)
            pop_sql = """
            SELECT 
              grade_num,
              SUM(count) as count
            FROM pop_den_year_grade
            WHERE denomination_slug = %s
            AND strike = %s
            AND year = %s
            GROUP BY grade_num
            """
            
            cur.execute(pop_sql, (denomination_slug, strike, year))
            pop_data = cur.fetchall()
            
            same_grade_count = 0
            higher_grade_count = 0
            total_in_denomination = 0
            
            for row in pop_data:
                g = row.get("grade_num")
                cnt = row.get("count", 0) or 0
                total_in_denomination += cnt
                
                if g == grade_num:
                    same_grade_count = cnt
                elif g > grade_num:
                    higher_grade_count += cnt
            
            # Subtract 1 from same_grade_count (exclude this cert itself)
            if same_grade_count > 0:
                same_grade_count -= 1
            
            # Build message
            # Stand Alone Finest Known = no other coins in this grade AND no coins graded higher
            if higher_grade_count == 0 and same_grade_count == 0:
                message = "Stand Alone Finest Known"
            # Finest Grade Known = multiple coins in the highest grade (no higher, but same grade exists)
            elif higher_grade_count == 0:
                total_in_grade = same_grade_count + 1  # +1 for this coin
                message = f"Finest Grade Known with {total_in_grade} coin{'s' if total_in_grade != 1 else ''} in this grade"
            else:
                message = f"{same_grade_count} other coin{'s' if same_grade_count != 1 else ''} in this grade, {higher_grade_count} coin{'s' if higher_grade_count != 1 else ''} graded higher"
            
            return {
                "same_grade_count": same_grade_count,
                "higher_grade_count": higher_grade_count,
                "total_in_denomination": total_in_denomination,
                "message": message,
                "denomination_slug": denomination_slug,
                "strike": strike,
                "year": year,
                "grade_num": grade_num
            }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

