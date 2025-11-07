#!/usr/bin/env python3
"""
Populate public verification tables from existing certs data.
This script:
1. Extracts coin data from certs and populates the coins table
2. Calculates and populates the pop_den_year_grade table for census
"""
import re
from db import pool
from psycopg.rows import dict_row

def extract_denomination(coin_name: str | None, year_and_name: str | None) -> str | None:
    """Extract denomination from coin_name or year_and_name."""
    text = coin_name or year_and_name or ''
    if not text:
        return None
    
    # Remove metal composition suffixes (-S, -G, -N)
    clean_text = re.sub(r'-[SGN]\b', '', text, flags=re.IGNORECASE)
    
    # 1. Shillings: "1 Shilling", "2 Shillings", "2.5 Shillings", "5 Shillings"
    shilling_match = re.search(r'\b(\d+(?:\.\d+)?)\s+Shilling(?:s)?\b', clean_text, re.IGNORECASE)
    if shilling_match:
        val = shilling_match.group(1)
        return f"{val} Shilling{'s' if val != '1' else ''}"
    
    # 2. Pennies: "1/4 Penny", "1/2 Penny", "1 Penny"
    penny_match = re.search(r'\b(1/4|1/2|1)\s+Penny\b', clean_text, re.IGNORECASE)
    if penny_match:
        return f"{penny_match.group(1)} Penny"
    
    # 2b. Pence: "6 Pence", "3 Pence", etc. (British/Rhodesian)
    pence_match = re.search(r'\b(\d+)\s+Pence\b', clean_text, re.IGNORECASE)
    if pence_match:
        return f"{pence_match.group(1)} Pence"
    
    # 3. Ponds: "1/2 Pond", "1 Pond"
    pond_match = re.search(r'\b(1/2|1)\s+Pond\b', clean_text, re.IGNORECASE)
    if pond_match:
        return f"{pond_match.group(1)} Pond"
    
    # 4. Rand: "R1-S" -> "R1", "R1-G" -> "R1", "R5-N" -> "R5", "R -S" -> "R1"
    rand_match = re.search(r'\bR\s*(\d+)?\b', clean_text, re.IGNORECASE)
    if rand_match:
        num = rand_match.group(1) or "1"  # Default to R1 if no number
        return f"R{num}"
    
    # 5. Cents: "5 Cent", "10 Cent", "20 Cent", "50 Cent", etc.
    cent_match = re.search(r'\b(\d+)\s+Cent\b', clean_text, re.IGNORECASE)
    if cent_match:
        return f"{cent_match.group(1)} Cent"
    
    # 6. Crown: "Crown" (common in British/Rhodesian coins)
    if re.search(r'\bCrown\b', clean_text, re.IGNORECASE):
        return "Crown"
    
    # 7. Try without year prefix
    without_year = re.sub(r'^\d{4}\s+', '', clean_text)
    if without_year != clean_text:
        # Try again with year removed
        shilling_match2 = re.search(r'\b(\d+(?:\.\d+)?)\s+Shilling(?:s)?\b', without_year, re.IGNORECASE)
        if shilling_match2:
            val = shilling_match2.group(1)
            return f"{val} Shilling{'s' if val != '1' else ''}"
        
        penny_match2 = re.search(r'\b(1/4|1/2|1)\s+Penny\b', without_year, re.IGNORECASE)
        if penny_match2:
            return f"{penny_match2.group(1)} Penny"
        
        pence_match2 = re.search(r'\b(\d+)\s+Pence\b', without_year, re.IGNORECASE)
        if pence_match2:
            return f"{pence_match2.group(1)} Pence"
        
        pond_match2 = re.search(r'\b(1/2|1)\s+Pond\b', without_year, re.IGNORECASE)
        if pond_match2:
            return f"{pond_match2.group(1)} Pond"
        
        rand_match2 = re.search(r'\bR\s*(\d+)?\b', without_year, re.IGNORECASE)
        if rand_match2:
            num = rand_match2.group(1) or "1"
            return f"R{num}"
        
        cent_match2 = re.search(r'\b(\d+)\s+Cent\b', without_year, re.IGNORECASE)
        if cent_match2:
            return f"{cent_match2.group(1)} Cent"
        
        crown_match2 = re.search(r'\bCrown\b', without_year, re.IGNORECASE)
        if crown_match2:
            return "Crown"
    
    return None

def denomination_to_slug(denomination: str) -> str:
    """Convert denomination to URL-friendly slug."""
    if not denomination:
        return "unknown"
    # Convert to lowercase, replace spaces and special chars with hyphens
    slug = re.sub(r'[^\w\s-]', '', denomination.lower())
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug.strip('-')

def extract_strike_type(grade1: str | None) -> str | None:
    """Extract strike type from grade1. AU and lower fall under MS."""
    if not grade1:
        return None
    upper = grade1.upper().strip()
    
    # Check for specific strike types first: PL, PF, PU
    if upper.startswith('PL') or 'PROOFLIKE' in upper:
        return 'PL'
    if upper.startswith('PF'):
        return 'PF'
    if upper.startswith('PU'):
        return 'PU'
    
    # MS, UNC, AU, and lower grades all fall under MS
    if (upper.startswith('MS') or upper.startswith('UNC') or 
        upper.startswith('AU') or upper.startswith('XF') or 
        upper.startswith('VF') or upper.startswith('F ') or 
        upper == 'F' or upper.startswith('VG') or 
        upper.startswith('G ') or upper == 'G' or 
        upper.startswith('AG') or upper.startswith('FR') or 
        upper.startswith('PO')):
        return 'MS'
    
    return None

def extract_grade_number(grade1: str | None) -> int | None:
    """Extract numeric grade from grade1 (1-70)."""
    if not grade1:
        return None
    
    # Look for numbers in the grade
    match = re.search(r'(\d+)', grade1)
    if match:
        num = int(match.group(1))
        if 1 <= num <= 70:
            return num
    
    return None

def extract_year(coin_name: str | None, year: str | None) -> int | None:
    """Extract year as integer."""
    if year:
        try:
            return int(year.strip())
        except:
            pass
    
    # Try to extract from coin_name
    if coin_name:
        match = re.search(r'^(\d{4})\b', coin_name)
        if match:
            try:
                return int(match.group(1))
            except:
                pass
    
    return None

def populate_coins_table():
    """Populate coins table from existing certs."""
    print("Populating coins table from certs...")
    
    sql = """
    SELECT id, serial_number, country, year, coin_name, addl1, addl2, addl3, grade1, grade2
    FROM certs
    WHERE country IS NOT NULL
    """
    
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        certs = cur.fetchall()
        
        print(f"Found {len(certs)} certs to process...")
        
        inserted = 0
        updated = 0
        
        for cert in certs:
            cert_id = cert['id']
            coin_name = cert.get('coin_name') or ''
            year_str = cert.get('year') or ''
            
            # Extract data
            denomination = extract_denomination(coin_name, None)
            if not denomination:
                continue  # Skip if we can't determine denomination
            
            denomination_slug = denomination_to_slug(denomination)
            year = extract_year(coin_name, year_str)
            strike = extract_strike_type(cert.get('grade1'))
            if not strike:
                strike = 'MS'  # Default to MS
            
            grade_text = cert.get('grade1') or 'Unknown'
            grade_num = extract_grade_number(cert.get('grade1'))
            if not grade_num:
                continue  # Skip if we can't determine grade
            
            # Determine metal from coin_name (S=Silver, G=Gold, N=Nickel)
            metal = None
            if re.search(r'-S\b', coin_name, re.IGNORECASE):
                metal = 'Silver'
            elif re.search(r'-G\b', coin_name, re.IGNORECASE):
                metal = 'Gold'
            elif re.search(r'-N\b', coin_name, re.IGNORECASE):
                metal = 'Nickel'
            
            # Insert or update coin
            upsert_sql = """
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
            """
            
            cur.execute(upsert_sql, (
                cert_id,
                cert.get('country') or 'South Africa',
                denomination,
                denomination_slug,
                year,
                cert.get('addl1'),  # Use addl1 as variety
                metal,
                strike,
                grade_text,
                grade_num,
                None,  # label_type
                None,  # pedigree
                None,  # notes
            ))
            
            if cur.rowcount > 0:
                inserted += 1
        
        print(f"✓ Inserted/updated {inserted} coins")
        return inserted

def populate_census_table():
    """Populate pop_den_year_grade table from coins."""
    print("Populating census table from coins...")
    
    # First, clear existing data
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM pop_den_year_grade")
        
        # Aggregate counts from coins
        sql = """
        INSERT INTO pop_den_year_grade (denomination_slug, strike, year, grade_num, count)
        SELECT 
          co.denomination_slug,
          co.strike,
          co.year,
          co.grade_num,
          COUNT(*) as count
        FROM coins co
        JOIN certs c ON c.id = co.cert_id
        WHERE c.status IN ('verified', 'pending')
          AND co.denomination_slug IS NOT NULL
          AND co.strike IS NOT NULL
          AND co.year IS NOT NULL
          AND co.grade_num IS NOT NULL
        GROUP BY co.denomination_slug, co.strike, co.year, co.grade_num
        """
        
        cur.execute(sql)
        count = cur.rowcount
        print(f"✓ Inserted {count} census records")
        return count

def set_display_numbers():
    """Set display_number from serial_number for existing certs."""
    print("Setting display_number from serial_number...")
    
    with pool.connection() as conn, conn.cursor() as cur:
        # Update certs to use serial_number as display_number if display_number is NULL
        sql = """
        UPDATE certs
        SET display_number = serial_number
        WHERE display_number IS NULL
        AND serial_number ~ '^(20\\d{2}-\\d{4}-\\d{3}|\\d{9}-\\d{3})$'
        """
        
        cur.execute(sql)
        updated = cur.rowcount
        print(f"✓ Set display_number for {updated} certs")
        
        # Set status to 'verified' for certs that have data
        sql2 = """
        UPDATE certs
        SET status = 'verified'
        WHERE status = 'pending'
        AND country IS NOT NULL
        AND coin_name IS NOT NULL
        """
        
        cur.execute(sql2)
        verified = cur.rowcount
        print(f"✓ Set status='verified' for {verified} certs")
        
        return updated, verified

if __name__ == "__main__":
    print("=" * 60)
    print("Populating Public Verification Tables")
    print("=" * 60)
    
    try:
        # Step 1: Set display_numbers
        set_display_numbers()
        
        # Step 2: Populate coins table
        populate_coins_table()
        
        # Step 3: Populate census table
        populate_census_table()
        
        print("\n" + "=" * 60)
        print("✅ All tables populated successfully!")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

