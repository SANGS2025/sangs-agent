-- ============================
-- SANGS Agent — Schema v1
-- (Render Postgres)
-- ============================

-- 1) Users (for app login/roles later; passwords will be hashed in app)
CREATE TABLE IF NOT EXISTS users (
  id            bigserial PRIMARY KEY,
  email         text UNIQUE NOT NULL,
  full_name     text,
  role          text NOT NULL DEFAULT 'staff', -- 'admin' | 'staff'
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 2) Consignments (your “Submission Number” = number)
CREATE TABLE IF NOT EXISTS consignments (
  id            bigserial PRIMARY KEY,
  number        text UNIQUE NOT NULL,         -- e.g., 12438484
  pedigree      text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 3) Consignment items (coins/notes within a consignment)
--    item_no is 1..N per consignment (used to form Serial Number)
CREATE TABLE IF NOT EXISTS consignment_items (
  id              bigserial PRIMARY KEY,
  consignment_id  bigint NOT NULL REFERENCES consignments(id) ON DELETE CASCADE,
  item_no         int    NOT NULL,     -- 1,2,3... within the consignment
  grade1          text,
  grade2          text,
  country         text,
  year_and_name   text,                -- e.g., "1965 R1-S"
  addl1           text,                -- Additional Information
  addl2           text,                -- Additional Information 2
  addl3           text,                -- Additional Information 3
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (consignment_id, item_no)
);

-- 4) Label KB (how to lay out labels for known coins)
--    'key' mirrors 'id' and helps with simple unique indexing/joins by “name id”.
CREATE TABLE IF NOT EXISTS label_kb (
  id            text PRIMARY KEY,      -- e.g., "1965 R1 Silver - English"
  key           text GENERATED ALWAYS AS (id) STORED,
  country       text NOT NULL,
  year          text NOT NULL,
  coin_name     text NOT NULL,         -- e.g., "R1-S"
  grade_label   text,                  -- e.g., "MS", "PF", etc. (optional)
  serial_format text,                  -- optional override
  addl1         text,
  addl2         text,
  addl3         text,
  aliases       text[]                 -- e.g., {"1965 r1 english","1965 r1-s eng"}
);

-- 5) Certs (eventual public verification)
CREATE TABLE IF NOT EXISTS certs (
  id              bigserial PRIMARY KEY,
  serial_number   text UNIQUE NOT NULL,        -- e.g., 12438484-001
  consignment_id  bigint REFERENCES consignments(id) ON DELETE SET NULL,
  item_id         bigint REFERENCES consignment_items(id) ON DELETE SET NULL,
  coin_name       text,
  grade           text,
  strike          text,                         -- PF/PL/MS (optional)
  img_front_url   text,
  img_back_url    text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- 6) Helper indexes for common queries
CREATE INDEX IF NOT EXISTS idx_items_consignment_id ON consignment_items(consignment_id);
CREATE INDEX IF NOT EXISTS idx_items_country ON consignment_items(country);
CREATE INDEX IF NOT EXISTS idx_items_year_name ON consignment_items(year_and_name);
CREATE INDEX IF NOT EXISTS idx_label_kb_key ON label_kb(key);

-- 7) Export View for Google Sheet (exact column order you want)
--    Serial Number = <consignments.number>-<LPAD(item_no, 3)>
DROP VIEW IF EXISTS v_export_consignment_items;
CREATE VIEW v_export_consignment_items AS
SELECT
  (c.number || '-' || lpad(ci.item_no::text, 3, '0')) AS "Serial Number",
  ci.grade1                            AS "Grade 1",
  ci.grade2                            AS "Grade 2",
  ci.country                           AS "Country",
  ci.year_and_name                     AS "Year and Name",
  ci.addl1                             AS "Additional Information",
  ci.addl2                             AS "Additional Information 2",
  ci.addl3                             AS "Additional Information 3",
  ci.consignment_id,
  ci.item_no
FROM consignment_items ci
JOIN consignments c ON c.id = ci.consignment_id
ORDER BY ci.consignment_id, ci.item_no;

