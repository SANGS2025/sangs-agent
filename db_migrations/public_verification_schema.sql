-- Public Verification Schema Migration
-- Extends certs table and adds new tables for public certificate verification

-- 1. Extend certs table with public verification fields
ALTER TABLE certs 
  ADD COLUMN IF NOT EXISTS display_number TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'verified', 'reslabbed', 'revoked')),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS superseded_by UUID,
  ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;

-- Add constraint for display_number regex patterns
-- Legacy: ^20\d{2}-\d{4}-\d{3}$ (e.g., 2025-1200-003)
-- 8-digit: ^\d{9}-\d{3}$ (e.g., 83420175-001)
-- Note: PostgreSQL doesn't support regex constraints directly, we'll validate in application

-- 2. Create coins table (1:1 with certs)
CREATE TABLE IF NOT EXISTS coins (
  cert_id INT PRIMARY KEY REFERENCES certs(id) ON DELETE CASCADE,
  country TEXT NOT NULL,
  denomination TEXT NOT NULL,
  denomination_slug TEXT NOT NULL,
  year INT,
  variety TEXT,
  metal TEXT,
  strike TEXT CHECK (strike IN ('MS', 'PF', 'PL', 'PU')),
  grade_text TEXT NOT NULL,
  grade_num INT CHECK (grade_num >= 1 AND grade_num <= 70),
  label_type TEXT,
  pedigree TEXT,
  notes TEXT
);

-- 3. Create images table
CREATE TABLE IF NOT EXISTS images (
  id SERIAL PRIMARY KEY,
  cert_id INT NOT NULL REFERENCES certs(id) ON DELETE CASCADE,
  type TEXT NOT NULL CHECK (type IN ('obv', 'rev')),
  path TEXT NOT NULL,
  w INT,
  h INT,
  checksum TEXT,
  UNIQUE(cert_id, type)
);

-- 4. Create cert_events table
CREATE TABLE IF NOT EXISTS cert_events (
  id SERIAL PRIMARY KEY,
  cert_id INT NOT NULL REFERENCES certs(id) ON DELETE CASCADE,
  type TEXT NOT NULL CHECK (type IN ('created', 'slabbed', 'revised', 'revoked')),
  actor TEXT,
  meta JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. Create population table for census
CREATE TABLE IF NOT EXISTS pop_den_year_grade (
  denomination_slug TEXT NOT NULL,
  strike TEXT NOT NULL CHECK (strike IN ('MS', 'PF', 'PL', 'PU')),
  year INT NOT NULL,
  grade_num INT NOT NULL CHECK (grade_num >= 1 AND grade_num <= 70),
  count INT NOT NULL DEFAULT 0,
  PRIMARY KEY (denomination_slug, strike, year, grade_num)
);

-- 6. Create indexes
CREATE INDEX IF NOT EXISTS idx_certs_display_number ON certs(display_number);
CREATE INDEX IF NOT EXISTS idx_certs_status ON certs(status);
CREATE INDEX IF NOT EXISTS idx_coins_denomination_slug ON coins(denomination_slug);
CREATE INDEX IF NOT EXISTS idx_coins_strike ON coins(strike);
CREATE INDEX IF NOT EXISTS idx_coins_year ON coins(year);
CREATE INDEX IF NOT EXISTS idx_images_cert_id ON images(cert_id);
CREATE INDEX IF NOT EXISTS idx_cert_events_cert_id ON cert_events(cert_id);
CREATE INDEX IF NOT EXISTS idx_pop_den_year_grade_lookup ON pop_den_year_grade(denomination_slug, strike, year, grade_num);

-- 7. Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 8. Trigger to auto-update updated_at
DROP TRIGGER IF EXISTS update_certs_updated_at ON certs;
CREATE TRIGGER update_certs_updated_at
  BEFORE UPDATE ON certs
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

