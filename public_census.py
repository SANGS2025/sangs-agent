# ~/sangs-agent/public_census.py
# Public census/explorer API
from typing import Optional, List, Dict
from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row
from db import pool

router = APIRouter(prefix="/api/census", tags=["public-census"])

@router.get("/countries")
def get_countries():
    """
    Get list of all countries with total coin counts.
    ZAR is combined into South Africa.
    Includes both verified and pending certs.
    """
    sql = """
    SELECT 
      CASE 
        WHEN co.country = 'ZAR' THEN 'South Africa'
        ELSE co.country
      END as country,
      COUNT(DISTINCT co.denomination_slug) as denomination_count,
      COUNT(*) as total_coins
    FROM coins co
    JOIN certs c ON c.id = co.cert_id
    WHERE co.country IS NOT NULL
    AND c.status IN ('verified', 'pending')
    GROUP BY 
      CASE 
        WHEN co.country = 'ZAR' THEN 'South Africa'
        ELSE co.country
      END
    ORDER BY total_coins DESC, country
    """
    
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        
        return [
            {
                "country": row["country"],
                "total": row["total_coins"] or 0,
                "denomination_count": row["denomination_count"] or 0,
            }
            for row in rows
        ]

@router.get("/regions")
def get_regions(country: str = Query(...)):
    """
    Get time periods/regions for a country.
    For South Africa, returns:
    - ZAR (Zuid-Afrikaansche Republiek) (1874-1902)
    - Union (Union of South Africa) (1910-1961)
    - Republic (Republic of South Africa) (1961-Present)
    """
    if country != "South Africa":
        # For other countries, return empty or just the country itself
        return []
    
    sql = """
    SELECT 
      region,
      period,
      total_coins
    FROM (
      SELECT 
        CASE 
          WHEN co.country = 'ZAR' THEN 'ZAR (Zuid-Afrikaansche Republiek)'
          WHEN co.country = 'South Africa' AND co.year IS NOT NULL AND co.year <= 1961 THEN 'Union'
          WHEN co.country = 'South Africa' AND co.year IS NOT NULL AND co.year > 1961 THEN 'Republic'
          WHEN co.country = 'South Africa' THEN 'Union & Republic'
          ELSE co.country
        END as region,
        CASE 
          WHEN co.country = 'ZAR' THEN '1874–1902'
          WHEN co.country = 'South Africa' AND co.year IS NOT NULL AND co.year <= 1961 THEN '1910–1961'
          WHEN co.country = 'South Africa' AND co.year IS NOT NULL AND co.year > 1961 THEN '1961–Present'
          WHEN co.country = 'South Africa' THEN '1910–Present'
          ELSE NULL
        END as period,
        COUNT(*) as total_coins
      FROM coins co
      JOIN certs c ON c.id = co.cert_id
      WHERE (co.country = 'South Africa' OR co.country = 'ZAR')
      AND c.status IN ('verified', 'pending')
      GROUP BY 
        CASE 
          WHEN co.country = 'ZAR' THEN 'ZAR (Zuid-Afrikaansche Republiek)'
          WHEN co.country = 'South Africa' AND co.year IS NOT NULL AND co.year <= 1961 THEN 'Union'
          WHEN co.country = 'South Africa' AND co.year IS NOT NULL AND co.year > 1961 THEN 'Republic'
          WHEN co.country = 'South Africa' THEN 'Union & Republic'
          ELSE co.country
        END,
        CASE 
          WHEN co.country = 'ZAR' THEN '1874–1902'
          WHEN co.country = 'South Africa' AND co.year IS NOT NULL AND co.year <= 1961 THEN '1910–1961'
          WHEN co.country = 'South Africa' AND co.year IS NOT NULL AND co.year > 1961 THEN '1961–Present'
          WHEN co.country = 'South Africa' THEN '1910–Present'
          ELSE NULL
        END
    ) subq
    ORDER BY 
      CASE 
        WHEN region = 'ZAR (Zuid-Afrikaansche Republiek)' THEN 1
        WHEN region = 'Union' THEN 2
        WHEN region = 'Republic' THEN 3
        ELSE 4
      END
    """
    
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        
        return [
            {
                "region": row["region"],
                "period": row["period"],
                "total": row["total_coins"] or 0,
            }
            for row in rows
        ]

@router.get("/sub-periods")
def get_sub_periods(country: str = Query(...), region: str = Query(...)):
    """
    Get sub-periods for a region.
    For Union: George V (1910-1936), Edward VIII (1936), George VI (1936-1952), Elizabeth II (1952-1961)
    For Republic: First Decimal (1961-1964), Second Decimal (1965-1988), Third Decimal (1989-2023), Fourth Decimal (2023-Present)
    """
    if country != "South Africa":
        return []
    
    if region == "Union":
        sql = """
        SELECT 
          sub_period,
          period,
          total_coins
        FROM (
          SELECT 
            CASE 
              WHEN co.year = 1936 THEN 'Edward VIII'
              WHEN co.year = 1952 THEN 'Elizabeth II'
              WHEN co.year >= 1910 AND co.year <= 1935 THEN 'George V'
              WHEN co.year >= 1937 AND co.year <= 1951 THEN 'George VI'
              WHEN co.year >= 1953 AND co.year <= 1961 THEN 'Elizabeth II'
              ELSE 'Unknown'
            END as sub_period,
            CASE 
              WHEN co.year = 1936 THEN '1936'
              WHEN co.year = 1952 THEN '1952–1961'
              WHEN co.year >= 1910 AND co.year <= 1935 THEN '1910–1936'
              WHEN co.year >= 1937 AND co.year <= 1951 THEN '1936–1952'
              WHEN co.year >= 1953 AND co.year <= 1961 THEN '1952–1961'
              ELSE NULL
            END as period,
            COUNT(*) as total_coins
          FROM coins co
          JOIN certs c ON c.id = co.cert_id
          WHERE co.country = 'South Africa'
          AND co.year IS NOT NULL
          AND co.year <= 1961
          AND c.status IN ('verified', 'pending')
          GROUP BY 
            CASE 
              WHEN co.year = 1936 THEN 'Edward VIII'
              WHEN co.year = 1952 THEN 'Elizabeth II'
              WHEN co.year >= 1910 AND co.year <= 1935 THEN 'George V'
              WHEN co.year >= 1937 AND co.year <= 1951 THEN 'George VI'
              WHEN co.year >= 1953 AND co.year <= 1961 THEN 'Elizabeth II'
              ELSE 'Unknown'
            END,
            CASE 
              WHEN co.year = 1936 THEN '1936'
              WHEN co.year = 1952 THEN '1952–1961'
              WHEN co.year >= 1910 AND co.year <= 1935 THEN '1910–1936'
              WHEN co.year >= 1937 AND co.year <= 1951 THEN '1936–1952'
              WHEN co.year >= 1953 AND co.year <= 1961 THEN '1952–1961'
              ELSE NULL
            END
        ) subq
        ORDER BY 
          CASE 
            WHEN sub_period = 'George V' THEN 1
            WHEN sub_period = 'Edward VIII' THEN 2
            WHEN sub_period = 'George VI' THEN 3
            WHEN sub_period = 'Elizabeth II' THEN 4
            ELSE 5
          END
        """
    elif region == "Republic":
        sql = """
        SELECT 
          sub_period,
          period,
          total_coins
        FROM (
          SELECT 
            CASE 
              WHEN co.year >= 1961 AND co.year <= 1964 THEN 'First Decimal Series'
              WHEN co.year >= 1965 AND co.year <= 1988 THEN 'Second Decimal Series'
              WHEN co.year >= 1989 AND co.year <= 2023 THEN 'Third Decimal Series'
              WHEN co.year > 2023 THEN 'Fourth Decimal Series'
              ELSE 'Unknown'
            END as sub_period,
            CASE 
              WHEN co.year >= 1961 AND co.year <= 1964 THEN '1961–1964'
              WHEN co.year >= 1965 AND co.year <= 1988 THEN '1965–1988'
              WHEN co.year >= 1989 AND co.year <= 2023 THEN '1989–2023'
              WHEN co.year > 2023 THEN '2023–Present'
              ELSE NULL
            END as period,
            COUNT(*) as total_coins
          FROM coins co
          JOIN certs c ON c.id = co.cert_id
          WHERE co.country = 'South Africa'
          AND co.year IS NOT NULL
          AND co.year >= 1961
          AND c.status IN ('verified', 'pending')
          GROUP BY 
            CASE 
              WHEN co.year >= 1961 AND co.year <= 1964 THEN 'First Decimal Series'
              WHEN co.year >= 1965 AND co.year <= 1988 THEN 'Second Decimal Series'
              WHEN co.year >= 1989 AND co.year <= 2023 THEN 'Third Decimal Series'
              WHEN co.year > 2023 THEN 'Fourth Decimal Series'
              ELSE 'Unknown'
            END,
            CASE 
              WHEN co.year >= 1961 AND co.year <= 1964 THEN '1961–1964'
              WHEN co.year >= 1965 AND co.year <= 1988 THEN '1965–1988'
              WHEN co.year >= 1989 AND co.year <= 2023 THEN '1989–2023'
              WHEN co.year > 2023 THEN '2023–Present'
              ELSE NULL
            END
        ) subq
        ORDER BY 
          CASE 
            WHEN sub_period = 'First Decimal Series' THEN 1
            WHEN sub_period = 'Second Decimal Series' THEN 2
            WHEN sub_period = 'Third Decimal Series' THEN 3
            WHEN sub_period = 'Fourth Decimal Series' THEN 4
            ELSE 5
          END
        """
    else:
        return []
    
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        
        return [
            {
                "sub_period": row["sub_period"],
                "period": row["period"],
                "total": row["total_coins"] or 0,
            }
            for row in rows
        ]

@router.get("/denominations")
def get_denominations(
    country: str = Query(default="South Africa"), 
    region: Optional[str] = Query(default=None),
    sub_period: Optional[str] = Query(default=None)
):
    """
    Get list of all denominations for a country (and optionally a region/sub-period) with total counts.
    Counts are based on actual coins, not pop_den_year_grade (which doesn't have country info).
    """
    # Build filters based on region and sub_period
    filters = []
    params = []
    
    if region:
        # Handle region names (with or without full name)
        if "ZAR" in region or region == "ZAR":
            filters.append("co.country = 'ZAR'")
        elif region == "Union" or "Union" in region:
            filters.append("co.country = 'South Africa'")
            filters.append("co.year IS NOT NULL")
            filters.append("co.year <= 1961")
            if sub_period:
                # Filter by monarch period - MUST filter by sub_period
                if sub_period == "George V":
                    filters.append("co.year >= 1910 AND co.year <= 1935")
                elif sub_period == "Edward VIII":
                    filters.append("co.year = 1936")
                elif sub_period == "George VI":
                    filters.append("co.year >= 1937 AND co.year <= 1951")
                elif sub_period == "Elizabeth II":
                    filters.append("(co.year = 1952 OR (co.year >= 1953 AND co.year <= 1961))")
        elif region == "Republic" or "Republic" in region:
            filters.append("co.country = 'South Africa'")
            filters.append("co.year IS NOT NULL")
            if sub_period:
                # Filter by decimal series - MUST filter by sub_period
                if sub_period == "First Decimal Series":
                    filters.append("co.year >= 1961 AND co.year <= 1964")
                elif sub_period == "Second Decimal Series":
                    filters.append("co.year >= 1965 AND co.year <= 1988")
                elif sub_period == "Third Decimal Series":
                    filters.append("co.year >= 1989 AND co.year <= 2023")
                elif sub_period == "Fourth Decimal Series":
                    filters.append("co.year > 2023")
            else:
                # No sub-period selected, show all Republic coins
                filters.append("co.year > 1961")
        else:
            filters.append("co.country = %s")
            params.append(country)
    else:
        # Include both South Africa and ZAR if country is South Africa
        if country == "South Africa":
            filters.append("(co.country = 'South Africa' OR co.country = 'ZAR')")
        else:
            filters.append("co.country = %s")
            params.append(country)
    
    where_clause = " AND ".join(filters) if filters else "1=1"
    
    sql = f"""
    SELECT 
      co.denomination_slug,
      co.denomination,
      COUNT(*) as total
    FROM coins co
    JOIN certs c ON c.id = co.cert_id
    WHERE {where_clause}
    AND c.status IN ('verified', 'pending')
    GROUP BY co.denomination_slug, co.denomination
    HAVING COUNT(*) > 0
    ORDER BY co.denomination
    """
    
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        
        # Only return denominations that have at least 1 coin
        result = []
        for row in rows:
            total = row["total"] or 0
            if total > 0:
                result.append({
                    "denomination_slug": row["denomination_slug"],
                    "denomination": row["denomination"],
                    "total": total,
                })
        
        return result

@router.get("/{denomination_slug}/strikes")
def get_strikes_for_denomination(denomination_slug: str):
    """
    Get available strikes for a denomination with counts.
    Returns which of PF/MS/PL/PU exist.
    Uses precomputed pop_den_year_grade for performance.
    """
    sql = """
    SELECT 
      strike,
      SUM(count) as count
    FROM pop_den_year_grade
    WHERE denomination_slug = %s
    GROUP BY strike
    ORDER BY 
      CASE strike
        WHEN 'MS' THEN 1
        WHEN 'PL' THEN 2
        WHEN 'PF' THEN 3
        WHEN 'PU' THEN 4
      END
    """
    
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (denomination_slug,))
        rows = cur.fetchall()
        
        if not rows:
            raise HTTPException(status_code=404, detail="Denomination not found")
        
        return [
            {
                "strike": row["strike"],
                "count": row["count"] or 0,
            }
            for row in rows
        ]

@router.get("/{denomination_slug}/{strike}/matrix")
def get_census_matrix(
    denomination_slug: str,
    strike: str,
    include_varieties: bool = Query(default=False)
):
    """
    Get census matrix: Year × Grades table.
    Returns array of {year, total, grades: {"1":0,...,"70":N}}
    Uses precomputed pop_den_year_grade for performance.
    """
    if strike not in ('MS', 'PF', 'PL', 'PU'):
        raise HTTPException(status_code=400, detail="Invalid strike type")
    
    # Build grade columns dynamically (1-70) from precomputed table
    grade_cols = []
    for g in range(1, 71):
        grade_cols.append(f"SUM(CASE WHEN grade_num = {g} THEN count ELSE 0 END) AS grade_{g}")
    
    sql = f"""
    SELECT 
      year,
      SUM(count) as total,
      {', '.join(grade_cols)}
    FROM pop_den_year_grade
    WHERE denomination_slug = %s
    AND strike = %s
    GROUP BY year
    ORDER BY year
    """
    
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (denomination_slug, strike))
        rows = cur.fetchall()
        
        if not rows:
            raise HTTPException(status_code=404, detail="No data found for this denomination and strike")
        
        result = []
        for row in rows:
            grades = {}
            for g in range(1, 71):
                grades[str(g)] = row.get(f"grade_{g}", 0) or 0
            
            result.append({
                "year": row["year"],
                "total": row["total"] or 0,
                "grades": grades,
            })
        
        return result

@router.get("/{denomination_slug}/{strike}/{year}/certs")
def get_certs_for_year_grade(
    denomination_slug: str,
    strike: str,
    year: int,
    grade: Optional[int] = Query(default=None)
):
    """
    Get list of certificates for a specific year (and optionally grade).
    """
    sql = """
    SELECT 
      c.display_number,
      co.grade_text,
      co.grade_num,
      co.variety
    FROM coins co
    JOIN certs c ON c.id = co.cert_id
    WHERE co.denomination_slug = %s
    AND co.strike = %s
    AND co.year = %s
    AND c.status = 'verified'
    """
    params = [denomination_slug, strike, year]
    
    if grade:
        sql += " AND co.grade_num = %s"
        params.append(grade)
    
    sql += " ORDER BY co.grade_num, c.display_number"
    
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        
        return [
            {
                "display_number": row["display_number"],
                "grade_text": row["grade_text"],
                "grade_num": row["grade_num"],
                "variety": row["variety"],
            }
            for row in rows
        ]

