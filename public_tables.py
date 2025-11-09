#!/usr/bin/env python3
"""
Utility helpers for keeping the public verification tables (`coins`,
`pop_den_year_grade`) in sync with the main `certs` table.

These routines are shared between the on-demand population script and
the ingestion pipeline so that newly ingested certificates immediately
appear in the public census/verification views.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Sequence, Tuple, Dict, Set

from psycopg.rows import dict_row

from db import pool

# ---------------------------------------------------------------------------
# Helpers to extract denomination / strike / grade information
# ---------------------------------------------------------------------------

DISPLAY_NUMBER_PATTERN = r"^(20\d{2}-\d{4}-\d{3}|\d{9}-\d{3})$"


def extract_denomination(coin_name: Optional[str], year_and_name: Optional[str]) -> Optional[str]:
    """Extract denomination from coin_name or year_and_name."""
    text = coin_name or year_and_name or ""
    if not text:
        return None

    # Remove metal composition suffixes (-S, -G, -N)
    clean_text = re.sub(r"-[SGN]\b", "", text, flags=re.IGNORECASE)

    # 1. Shillings: "1 Shilling", "2 Shillings", "2.5 Shillings", "5 Shillings"
    shilling_match = re.search(r"\b(\d+(?:\.\d+)?)\s+Shilling(?:s)?\b", clean_text, re.IGNORECASE)
    if shilling_match:
        val = shilling_match.group(1)
        return f"{val} Shilling{'s' if val != '1' else ''}"

    # 2. Pennies: "1/4 Penny", "1/2 Penny", "1 Penny"
    penny_match = re.search(r"\b(1/4|1/2|1)\s+Penny\b", clean_text, re.IGNORECASE)
    if penny_match:
        return f"{penny_match.group(1)} Penny"

    # 2b. Pence: "6 Pence", "3 Pence", etc.
    pence_match = re.search(r"\b(\d+)\s+Pence\b", clean_text, re.IGNORECASE)
    if pence_match:
        return f"{pence_match.group(1)} Pence"

    # 3. Ponds: "1/2 Pond", "1 Pond"
    pond_match = re.search(r"\b(1/2|1)\s+Pond\b", clean_text, re.IGNORECASE)
    if pond_match:
        return f"{pond_match.group(1)} Pond"

    # 4. Rand: "R1-S" -> "R1", "R1-G" -> "R1", "R5-N" -> "R5", "R -S" -> "R1"
    rand_match = re.search(r"\bR\s*(\d+)?\b", clean_text, re.IGNORECASE)
    if rand_match:
        num = rand_match.group(1) or "1"  # Default to R1 if no number
        return f"R{num}"

    # 5. Cents: "5 Cent", "10 Cent", "20 Cent", "50 Cent", etc.
    cent_match = re.search(r"\b(\d+)\s+Cent\b", clean_text, re.IGNORECASE)
    if cent_match:
        return f"{cent_match.group(1)} Cent"

    # 6. Crown (common in British/Rhodesian coins)
    if re.search(r"\bCrown\b", clean_text, re.IGNORECASE):
        return "Crown"

    # 7. Try without year prefix
    without_year = re.sub(r"^\d{4}\s+", "", clean_text)
    if without_year != clean_text:
        # Try again with year removed
        shilling_match2 = re.search(r"\b(\d+(?:\.\d+)?)\s+Shilling(?:s)?\b", without_year, re.IGNORECASE)
        if shilling_match2:
            val = shilling_match2.group(1)
            return f"{val} Shilling{'s' if val != '1' else ''}"

        penny_match2 = re.search(r"\b(1/4|1/2|1)\s+Penny\b", without_year, re.IGNORECASE)
        if penny_match2:
            return f"{penny_match2.group(1)} Penny"

        pence_match2 = re.search(r"\b(\d+)\s+Pence\b", without_year, re.IGNORECASE)
        if pence_match2:
            return f"{pence_match2.group(1)} Pence"

        pond_match2 = re.search(r"\b(1/2|1)\s+Pond\b", without_year, re.IGNORECASE)
        if pond_match2:
            return f"{pond_match2.group(1)} Pond"

        rand_match2 = re.search(r"\bR\s*(\d+)?\b", without_year, re.IGNORECASE)
        if rand_match2:
            num = rand_match2.group(1) or "1"
            return f"R{num}"

        cent_match2 = re.search(r"\b(\d+)\s+Cent\b", without_year, re.IGNORECASE)
        if cent_match2:
            return f"{cent_match2.group(1)} Cent"

        crown_match2 = re.search(r"\bCrown\b", without_year, re.IGNORECASE)
        if crown_match2:
            return "Crown"

    return None


def denomination_to_slug(denomination: Optional[str]) -> str:
    """Convert denomination to URL-friendly slug."""
    if not denomination:
        return "unknown"
    processed = denomination.lower()
    processed = processed.replace("½", "half")
    processed = processed.replace(".", "-")
    processed = processed.replace("/", "-")
    processed = re.sub(r"[^a-z0-9\s-]", "", processed)
    slug = re.sub(r"[-\s]+", "-", processed)
    return slug.strip("-")


def extract_strike_type(grade1: Optional[str]) -> Optional[str]:
    """Extract strike type from grade1. AU and lower fall under MS."""
    if not grade1:
        return None
    upper = grade1.upper().strip()

    # Check for specific strike types first: PL, PF, PU
    if upper.startswith("PL") or "PROOFLIKE" in upper:
        return "PL"
    if upper.startswith("PF"):
        return "PF"
    if upper.startswith("PU"):
        return "PU"

    # MS, UNC, AU, and lower grades all fall under MS
    if (
        upper.startswith("MS")
        or upper.startswith("UNC")
        or upper.startswith("AU")
        or upper.startswith("XF")
        or upper.startswith("VF")
        or upper.startswith("F ")
        or upper == "F"
        or upper.startswith("VG")
        or upper.startswith("G ")
        or upper == "G"
        or upper.startswith("AG")
        or upper.startswith("FR")
        or upper.startswith("PO")
    ):
        return "MS"

    return None


def extract_grade_number(grade1: Optional[str]) -> Optional[int]:
    """Extract numeric grade from grade1 (1-70)."""
    if not grade1:
        return None

    match = re.search(r"(\d+)", grade1)
    if match:
        num = int(match.group(1))
        if 1 <= num <= 70:
            return num

    return None


def extract_year(coin_name: Optional[str], year: Optional[str]) -> Optional[int]:
    """Extract year as integer."""
    if year:
        try:
            return int(year.strip())
        except Exception:
            pass

    if coin_name:
        match = re.match(r"^(\d{4})\b", coin_name)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    return None


PopulationKey = Tuple[str, str, int]


def normalize_country(original: Optional[str], year: Optional[int]) -> Optional[str]:
    """
    Normalize the coin country so that eras align with year ranges.
    ZAR applies only through 1902; anything afterwards defaults to South Africa unless
    the original country is one of the Rhodesia variants or other foreign mints.
    """
    if year is None:
        return original or "South Africa"

    if original:
        lower = original.lower()
        rhodesia_variants = {
            "rhodesia",
            "southern rhodesia",
            "rhodesia & nyasaland",
            "rhodesia and nyasaland",
            "malawi",
            "isle of man",
            "british west africa",
            "united kingdom",
            "australia",
            "new zeland",
            "new zealand",
        }
        if lower in rhodesia_variants:
            return original

    if year <= 1902:
        return "ZAR"

    return "South Africa"


def _sanitize_serials(serials: Iterable[str]) -> Sequence[str]:
    seen: Set[str] = set()
    result: list[str] = []
    for serial in serials:
        s = (serial or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        result.append(s)
    return result


def _set_display_numbers(cur, serials: Optional[Sequence[str]]) -> Tuple[int, int]:
    params = ()
    filter_clause = ""
    if serials:
        filter_clause = "AND serial_number = ANY(%s)"
        params = (serials,)

    cur.execute(
        f"""
        UPDATE certs
        SET display_number = serial_number
        WHERE display_number IS NULL
          AND serial_number ~ %s
          {filter_clause}
        """,
        (DISPLAY_NUMBER_PATTERN, *params),
    )
    display_updated = cur.rowcount

    cur.execute(
        f"""
        UPDATE certs
        SET status = 'verified'
        WHERE status = 'pending'
          AND country IS NOT NULL
          AND coin_name IS NOT NULL
          {filter_clause}
        """,
        params,
    )
    status_updated = cur.rowcount

    return display_updated, status_updated


def _get_existing_coin_key(cur, cert_id: int) -> Optional[PopulationKey]:
    cur.execute(
        """
        SELECT denomination_slug, strike, year
        FROM coins
        WHERE cert_id = %s
        """,
        (cert_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    slug, strike, year = row
    if slug and strike and year is not None:
        return slug, strike, year
    return None


def _upsert_coin(cur, cert: dict) -> Tuple[Set[PopulationKey], bool]:
    affected_keys: Set[PopulationKey] = set()
    cert_id = cert["id"]
    prev_key = _get_existing_coin_key(cur, cert_id)
    if prev_key:
        affected_keys.add(prev_key)

    country = cert.get("country")
    coin_name = cert.get("coin_name") or ""
    year_str = cert.get("year") or ""
    grade1 = cert.get("grade1")

    # Determine derived fields
    denomination = extract_denomination(coin_name, None)
    grade_num = extract_grade_number(grade1)
    strike = extract_strike_type(grade1) or "MS"
    year = extract_year(coin_name, year_str)
    metal = None
    if re.search(r"-S\b", coin_name, re.IGNORECASE):
        metal = "Silver"
    elif re.search(r"-G\b", coin_name, re.IGNORECASE):
        metal = "Gold"
    elif re.search(r"-N\b", coin_name, re.IGNORECASE):
        metal = "Nickel"

    should_delete = False
    if not country or not denomination or not grade_num or year is None:
        should_delete = True

    if should_delete:
        cur.execute("DELETE FROM coins WHERE cert_id = %s", (cert_id,))
        return affected_keys, False

    denomination_slug = denomination_to_slug(denomination)
    normalized_country = normalize_country(country, year)
    affected_keys.add((denomination_slug, strike, year))

    cur.execute(
        """
        INSERT INTO coins (
          cert_id, country, denomination, denomination_slug, year, variety,
          metal, strike, grade_text, grade_num, label_type, pedigree, notes
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (cert_id) DO UPDATE SET
          country = EXCLUDED.country,
          denomination = EXCLUDED.denomination,
          denomination_slug = EXCLUDED.denomination_slug,
          year = EXCLUDED.year,
          variety = EXCLUDED.variety,
          metal = EXCLUDED.metal,
          strike = EXCLUDED.strike,
          grade_text = EXCLUDED.grade_text,
          grade_num = EXCLUDED.grade_num,
          label_type = EXCLUDED.label_type,
          pedigree = EXCLUDED.pedigree,
          notes = EXCLUDED.notes
        """,
        (
            cert_id,
            normalized_country or "South Africa",
            denomination,
            denomination_slug,
            year,
            cert.get("addl1"),
            metal,
            strike,
            grade1 or "Unknown",
            grade_num,
            None,
            None,
            None,
        ),
    )

    return affected_keys, True


def _refresh_population(cur, keys: Set[PopulationKey]) -> int:
    updated = 0
    for slug, strike, year in keys:
        if not slug or not strike or year is None:
            continue

        cur.execute(
            """
            DELETE FROM pop_den_year_grade
            WHERE denomination_slug = %s
              AND strike = %s
              AND year = %s
            """,
            (slug, strike, year),
        )

        cur.execute(
            """
            INSERT INTO pop_den_year_grade (denomination_slug, strike, year, grade_num, count)
            SELECT
              co.denomination_slug,
              co.strike,
              co.year,
              co.grade_num,
              COUNT(*) as count
            FROM coins co
            JOIN certs c ON c.id = co.cert_id
            WHERE co.denomination_slug = %s
              AND co.strike = %s
              AND co.year = %s
              AND c.status IN ('verified', 'pending')
            GROUP BY co.denomination_slug, co.strike, co.year, co.grade_num
            """,
            (slug, strike, year),
        )

        updated += 1

    return updated


def update_public_tables_for_serials(serials: Iterable[str]) -> Dict[str, int]:
    serial_list = _sanitize_serials(serials)
    if not serial_list:
        return {"serials_processed": 0}

    stats = {
        "serials_processed": len(serial_list),
        "display_numbers_updated": 0,
        "status_verified": 0,
        "coins_upserted": 0,
        "coins_deleted": 0,
        "population_refreshed": 0,
    }

    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        display_updated, status_updated = _set_display_numbers(cur, serial_list)
        stats["display_numbers_updated"] = display_updated
        stats["status_verified"] = status_updated

        cur.execute(
            """
            SELECT id, serial_number, country, year, coin_name,
                   addl1, addl2, addl3, grade1, grade2
            FROM certs
            WHERE serial_number = ANY(%s)
            """,
            (serial_list,),
        )
        certs = cur.fetchall()

        affected_keys: Set[PopulationKey] = set()

        for cert in certs:
            keys_before = len(affected_keys)
            new_keys, inserted = _upsert_coin(cur, cert)
            affected_keys.update(new_keys)
            if inserted:
                stats["coins_upserted"] += 1
            elif new_keys and len(affected_keys) > keys_before:
                stats["coins_deleted"] += 1

        refreshed = _refresh_population(cur, affected_keys)
        stats["population_refreshed"] = refreshed

        conn.commit()

    return stats


def rebuild_public_tables() -> Dict[str, int]:
    stats = {
        "display_numbers_updated": 0,
        "status_verified": 0,
        "coins_processed": 0,
        "population_rows": 0,
    }

    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        display_updated, status_updated = _set_display_numbers(cur, None)
        stats["display_numbers_updated"] = display_updated
        stats["status_verified"] = status_updated

        cur.execute("DELETE FROM coins")

        cur.execute(
            """
            SELECT id, serial_number, country, year, coin_name,
                   addl1, addl2, addl3, grade1, grade2
            FROM certs
            WHERE country IS NOT NULL
            """
        )
        certs = cur.fetchall()

        affected_keys: Set[PopulationKey] = set()

        for cert in certs:
            new_keys, inserted = _upsert_coin(cur, cert)
            if inserted:
                stats["coins_processed"] += 1
            affected_keys.update(new_keys)

        cur.execute("DELETE FROM pop_den_year_grade")
        stats["population_rows"] = _refresh_population(cur, affected_keys)

        conn.commit()

    return stats


__all__ = [
    "update_public_tables_for_serials",
    "rebuild_public_tables",
]
