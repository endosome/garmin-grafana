-- Plan item 9, lever 2: the record fields every chart reads become stored generated columns holding the raw integer, so the
-- typed view reads a column instead of parsing JSONB. The JSONB stays the truth and every writer keeps updating only it;
-- a value that is not a number (an array where the profile has a scalar) leaves the column NULL, and the view then falls back to parsing the JSONB.
-- Field numbers: 0 position_lat, 1 position_long, 2 altitude, 3 heart_rate, 4 cadence, 5 distance, 6 speed, 7 power,
-- 13 temperature, 73 enhanced_speed, 78 enhanced_altitude (looked up by views.py from the profile, which checks them).
ALTER TABLE fit.records
 ADD COLUMN f0 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'0') = 'number' THEN (fields->>'0')::numeric END) STORED,
 ADD COLUMN f1 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'1') = 'number' THEN (fields->>'1')::numeric END) STORED,
 ADD COLUMN f2 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'2') = 'number' THEN (fields->>'2')::numeric END) STORED,
 ADD COLUMN f3 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'3') = 'number' THEN (fields->>'3')::numeric END) STORED,
 ADD COLUMN f4 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'4') = 'number' THEN (fields->>'4')::numeric END) STORED,
 ADD COLUMN f5 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'5') = 'number' THEN (fields->>'5')::numeric END) STORED,
 ADD COLUMN f6 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'6') = 'number' THEN (fields->>'6')::numeric END) STORED,
 ADD COLUMN f7 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'7') = 'number' THEN (fields->>'7')::numeric END) STORED,
 ADD COLUMN f13 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'13') = 'number' THEN (fields->>'13')::numeric END) STORED,
 ADD COLUMN f73 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'73') = 'number' THEN (fields->>'73')::numeric END) STORED,
 ADD COLUMN f78 numeric GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(fields->'78') = 'number' THEN (fields->>'78')::numeric END) STORED;
