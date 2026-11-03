-- Helpers for the typed views, which are generated from the profile at migrate time (views.py), not committed.
CREATE FUNCTION fit.type_name(type_name text, raw bigint) RETURNS text
LANGUAGE sql STABLE AS $$
 SELECT t.value_name FROM fit.profile_types t
 WHERE t.sdk_version = (SELECT sdk_version FROM fit.profile_versions ORDER BY loaded_at DESC, sdk_version DESC LIMIT 1)
 AND t.type_name = $1 AND t.raw_value = $2
$$;
CREATE FUNCTION fit.fit_time(raw bigint) RETURNS timestamptz
LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN raw >= 268435456 THEN to_timestamp(raw + 631065600) END $$;
-- A file can put an array or a string where the profile has a number; the views then show NULL instead of failing.
CREATE FUNCTION fit.num(j jsonb) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE jsonb_typeof(j) WHEN 'number' THEN (j #>> '{}')::numeric
  WHEN 'array' THEN CASE WHEN jsonb_typeof(j->0) = 'number' THEN (j->>0)::numeric END END
$$;
CREATE VIEW fit.v_dev_fields AS
 SELECT file_id, (fields->>'0') || ':' || (fields->>'1') AS dev_key,
        CASE jsonb_typeof(fields->'3') WHEN 'array' THEN fields->'3'->>0 ELSE fields->>'3' END AS name,
        CASE jsonb_typeof(fields->'8') WHEN 'array' THEN fields->'8'->>0 ELSE fields->>'8' END AS units
 FROM fit.messages WHERE global_num = 206;
