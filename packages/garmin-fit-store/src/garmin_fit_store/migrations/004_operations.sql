-- Edit primitives (plan items 13 and 14). Every one copies the source file into a new fit.files row (parent_id, operation)
-- and edits the copy; the original is never touched. Message and field numbers are looked up in the loaded profile by
-- name, so nothing here carries a Garmin number table.

CREATE FUNCTION fit.msg_num(name text) RETURNS integer LANGUAGE sql STABLE AS $$
 SELECT message_number FROM fit.profile_messages
 WHERE message_name = $1 AND sdk_version = (SELECT sdk_version FROM fit.profile_versions ORDER BY loaded_at DESC, sdk_version DESC LIMIT 1)
$$;
CREATE FUNCTION fit.field_num(message text, name text) RETURNS integer LANGUAGE sql STABLE AS $$
 SELECT f.field_number FROM fit.profile_fields f
 WHERE f.message_number = fit.msg_num($1) AND f.field_name = $2
 AND f.sdk_version = (SELECT sdk_version FROM fit.profile_versions ORDER BY loaded_at DESC, sdk_version DESC LIMIT 1)
$$;
CREATE FUNCTION fit.enum_raw(type_name text, value_name text) RETURNS bigint LANGUAGE sql STABLE AS $$
 SELECT raw_value FROM fit.profile_types
 WHERE type_name = $1 AND value_name = $2
 AND sdk_version = (SELECT sdk_version FROM fit.profile_versions ORDER BY loaded_at DESC, sdk_version DESC LIMIT 1)
$$;

-- {"message number": [field numbers]} of every calendar-time field (date_time and local_date_time) except the device's own clock.
CREATE FUNCTION fit.time_carriers() RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT coalesce(jsonb_object_agg(message_number::text, numbers), '{}'::jsonb) FROM (
  SELECT message_number, jsonb_agg(field_number ORDER BY field_number) AS numbers FROM fit.profile_fields
  WHERE field_type IN ('date_time', 'local_date_time') AND field_name <> 'system_timestamp'
  AND sdk_version = (SELECT sdk_version FROM fit.profile_versions ORDER BY loaded_at DESC, sdk_version DESC LIMIT 1)
  GROUP BY message_number) c
$$;

-- A calendar time (FIT seconds, at least 2^28) shifted by delta when it is at least threshold (NULL: always).
CREATE FUNCTION fit.shift_value(v jsonb, delta bigint, threshold bigint DEFAULT NULL) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE jsonb_typeof(v)
  WHEN 'number' THEN CASE WHEN (v #>> '{}')::numeric >= greatest(268435456, coalesce(threshold, 0)) THEN to_jsonb((v #>> '{}')::bigint + delta) ELSE v END
  WHEN 'array' THEN (SELECT coalesce(jsonb_agg(fit.shift_value(e, delta, threshold) ORDER BY o), '[]'::jsonb) FROM jsonb_array_elements(v) WITH ORDINALITY AS t(e, o))
  ELSE v END
$$;

-- fields with every calendar-time field shifted by delta seconds (the timestamp field 253 of any message included).
CREATE FUNCTION fit.shift_fields(message integer, fields jsonb, delta bigint, carriers jsonb, threshold bigint DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE k text; result jsonb := fields;
BEGIN
 IF delta = 0 THEN RETURN fields; END IF;
 FOR k IN SELECT DISTINCT e::text FROM jsonb_array_elements_text(coalesce(carriers->message::text, '[]'::jsonb) || '[253]'::jsonb) e LOOP
  IF result ? k THEN result := jsonb_set(result, ARRAY[k], fit.shift_value(result->k, delta, threshold)); END IF;
 END LOOP;
 RETURN result;
END $$;

CREATE FUNCTION fit.shift_dev(dev jsonb, delta bigint, keys text[], threshold bigint DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE k text; result jsonb := dev;
BEGIN
 IF delta = 0 OR keys IS NULL THEN RETURN dev; END IF;
 FOREACH k IN ARRAY keys LOOP
  IF result ? k THEN result := jsonb_set(result, ARRAY[k], fit.shift_value(result->k, delta, threshold)); END IF;
 END LOOP;
 RETURN result;
END $$;

-- Copy a file into a new row. delta shifts calendar times by that many seconds, for rows from delta_from on (or all rows when
-- delta_from is NULL); records in [drop_from, drop_to) are left out. Returns the new file id.
CREATE FUNCTION fit.copy_file(src bigint, op jsonb, delta bigint DEFAULT 0, delta_from timestamptz DEFAULT NULL,
                              drop_from timestamptz DEFAULT NULL, drop_to timestamptz DEFAULT NULL) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE new_id bigint; carriers jsonb := fit.time_carriers(); dev_keys text[]; rec integer := fit.msg_num('record');
 threshold bigint := CASE WHEN delta_from IS NULL THEN NULL ELSE extract(epoch FROM delta_from)::bigint - 631065600 END;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM fit.files WHERE id = src) THEN RAISE EXCEPTION 'no fit.files row %', src; END IF;
 INSERT INTO fit.files(source_id, sha256, garmin_activity_id, parent_id, operation, protocol_version, profile_number, sdk_version, message_count)
  SELECT source_id, sha256(convert_to(src::text || op::text || clock_timestamp()::text, 'UTF8')), garmin_activity_id, src, op,
         protocol_version, profile_number, sdk_version, 0 FROM fit.files WHERE id = src RETURNING id INTO new_id;
 INSERT INTO fit.definitions SELECT new_id, def_id, global_num, arch, fields, dev_fields FROM fit.definitions WHERE file_id = src;
 SELECT array_agg((fields->>'0') || ':' || (fields->>'1')) INTO dev_keys FROM fit.messages
  WHERE file_id = src AND global_num = fit.msg_num('field_description') AND fields->>fit.field_num('field_description', 'native_field_num')::text = '253';
 INSERT INTO fit.messages
  SELECT new_id, seq, def_id, global_num,
         time + make_interval(secs => d), fit.shift_fields(global_num, fields, d, carriers, threshold), fit.shift_dev(dev, d, dev_keys, threshold)
  FROM (SELECT m.*, CASE WHEN delta_from IS NULL OR m.time IS NULL OR m.time >= delta_from THEN delta ELSE 0 END AS d
        FROM fit.messages m WHERE m.file_id = src) m;
 INSERT INTO fit.records
  SELECT new_id, seq, def_id, time + make_interval(secs => d), fit.shift_fields(rec, fields, d, carriers, threshold), fit.shift_dev(dev, d, dev_keys, threshold)
  FROM (SELECT r.*, CASE WHEN delta_from IS NULL THEN delta WHEN r.time >= delta_from THEN delta ELSE 0 END AS d
        FROM fit.records r WHERE r.file_id = src
        AND NOT (drop_from IS NOT NULL AND r.time >= drop_from AND r.time < drop_to)) r;
 PERFORM fit.refresh_file(new_id);
 RETURN new_id;
END $$;

-- message_count, start_time and end_time of a file from its rows.
CREATE FUNCTION fit.refresh_file(file bigint) RETURNS void LANGUAGE sql AS $$
 UPDATE fit.files f SET message_count = c.n, start_time = c.t0, end_time = c.t1
 FROM (SELECT count(*) AS n, min(time) AS t0, max(time) AS t1 FROM (
        SELECT time FROM fit.messages WHERE file_id = $1 UNION ALL SELECT time FROM fit.records WHERE file_id = $1) u) c
 WHERE f.id = $1
$$;

-- Time Adjuster
CREATE FUNCTION fit.time_shift(src bigint, seconds bigint) RETURNS bigint LANGUAGE sql AS $$
 SELECT fit.copy_file($1, jsonb_build_object('operation', 'time_shift', 'seconds', $2), $2)
$$;

-- Device Changer: manufacturer and garmin_product by their profile names (or NULL to keep), serial number as a number.
CREATE FUNCTION fit.change_device(src bigint, manufacturer text, product text, serial bigint) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE new_id bigint;
 mfr_raw bigint := CASE WHEN manufacturer IS NULL THEN NULL ELSE fit.enum_raw('manufacturer', manufacturer) END;
 prod_raw bigint := CASE WHEN product IS NULL THEN NULL ELSE coalesce(fit.enum_raw('garmin_product', product), CASE WHEN product ~ '^[0-9]+$' THEN product::bigint END) END;
 
BEGIN
 IF manufacturer IS NOT NULL AND mfr_raw IS NULL THEN RAISE EXCEPTION 'unknown manufacturer %', manufacturer; END IF;
 IF product IS NOT NULL AND prod_raw IS NULL THEN RAISE EXCEPTION 'unknown product %', product; END IF;
 new_id := fit.copy_file(src, jsonb_build_object('operation', 'change_device', 'manufacturer', manufacturer, 'product', product, 'serial_number', serial));
 UPDATE fit.messages m SET fields = fit.set_defined(m, 'manufacturer', to_jsonb(mfr_raw))
  WHERE m.file_id = new_id AND mfr_raw IS NOT NULL AND m.global_num IN (fit.msg_num('file_id'), fit.msg_num('device_info'))
  AND (m.global_num = fit.msg_num('file_id') OR coalesce(m.fields->>(fit.field_num('device_info', 'device_index'))::text, '0') = '0');
 UPDATE fit.messages m SET fields = fit.set_defined(m, 'product', to_jsonb(prod_raw))
  WHERE m.file_id = new_id AND prod_raw IS NOT NULL AND m.global_num IN (fit.msg_num('file_id'), fit.msg_num('device_info'))
  AND (m.global_num = fit.msg_num('file_id') OR coalesce(m.fields->>(fit.field_num('device_info', 'device_index'))::text, '0') = '0');
 UPDATE fit.messages m SET fields = fit.set_defined(m, 'serial_number', to_jsonb(serial))
  WHERE m.file_id = new_id AND serial IS NOT NULL AND m.global_num IN (fit.msg_num('file_id'), fit.msg_num('device_info'))
  AND (m.global_num = fit.msg_num('file_id') OR coalesce(m.fields->>(fit.field_num('device_info', 'device_index'))::text, '0') = '0');
 UPDATE fit.messages m SET fields = fit.set_defined(m, 'garmin_product', to_jsonb(prod_raw))
  WHERE m.file_id = new_id AND prod_raw IS NOT NULL AND m.global_num = fit.msg_num('device_info')
  AND (coalesce(m.fields->>(fit.field_num('device_info', 'device_index'))::text, '0') = '0');
 RETURN new_id;
END $$;

-- fields of a message with the named field set, when the row has it or its definition lists it (a definition is never extended here).
CREATE FUNCTION fit.set_defined(m fit.messages, name text, value jsonb) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE n integer := fit.field_num(CASE m.global_num WHEN fit.msg_num('file_id') THEN 'file_id' ELSE 'device_info' END, name);
BEGIN
 IF n IS NULL THEN RETURN m.fields; END IF;
 IF m.fields ? n::text OR EXISTS (SELECT 1 FROM fit.definitions d, jsonb_array_elements(d.fields) e
                                  WHERE d.file_id = m.file_id AND d.def_id = m.def_id AND (e->>0)::int = n) THEN
  RETURN jsonb_set(m.fields, ARRAY[n::text], value);
 END IF;
 RETURN m.fields;
END $$;

-- Totals of the laps and sessions of a file from the records that remain: start and end clamped out of any removed range,
-- elapsed and timer time, distance, and the average / maximum / minimum of every record field a lap or session summarises
-- by name (avg_heart_rate from heart_rate, enhanced_max_speed from enhanced_speed, ...), where the scales agree.
CREATE FUNCTION fit.recompute_totals(file bigint, removed_from timestamptz DEFAULT NULL, removed_to timestamptz DEFAULT NULL,
                                     closed boolean DEFAULT false) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
 summary text; summary_num integer; start_n integer; end_n integer; elapsed_n integer; timer_n integer; dist_n integer;
 rec_dist_n integer := fit.field_num('record', 'distance');
 row_ record; s timestamptz; t timestamptz; overlap numeric; stat record; value numeric; first_d numeric; last_d numeric; patch jsonb;
BEGIN
 FOREACH summary IN ARRAY ARRAY['lap', 'session'] LOOP
  summary_num := fit.msg_num(summary);
  start_n := fit.field_num(summary, 'start_time'); end_n := fit.field_num(summary, 'timestamp');
  elapsed_n := fit.field_num(summary, 'total_elapsed_time'); timer_n := fit.field_num(summary, 'total_timer_time');
  dist_n := fit.field_num(summary, 'total_distance');
  FOR row_ IN SELECT * FROM fit.messages WHERE file_id = file AND global_num = summary_num AND fields ? start_n::text AND fields ? end_n::text LOOP
   s := fit.fit_time((row_.fields->>start_n::text)::bigint); t := fit.fit_time((row_.fields->>end_n::text)::bigint);
   patch := '{}'::jsonb; overlap := 0;
   IF removed_from IS NOT NULL THEN
    IF s >= removed_from AND s < removed_to THEN s := CASE WHEN closed THEN removed_from ELSE removed_to END; END IF;
    IF t > removed_from AND t < removed_to THEN t := removed_from; END IF;
    IF NOT closed THEN overlap := greatest(0, extract(epoch FROM least(t, removed_to) - greatest(s, removed_from))); END IF;
   END IF;
   IF t < s THEN t := s; END IF;
   patch := patch || jsonb_build_object(start_n::text, extract(epoch FROM s)::bigint - 631065600, end_n::text, extract(epoch FROM t)::bigint - 631065600);
   IF row_.fields ? elapsed_n::text THEN
    patch := patch || jsonb_build_object(elapsed_n::text, round(extract(epoch FROM t - s) * 1000));
   END IF;
   IF row_.fields ? timer_n::text THEN
    patch := patch || jsonb_build_object(timer_n::text, round((extract(epoch FROM t - s) - overlap) * 1000));
   END IF;
   IF rec_dist_n IS NOT NULL AND dist_n IS NOT NULL AND row_.fields ? dist_n::text THEN
    SELECT (array_agg((fields->>rec_dist_n::text)::numeric ORDER BY time) FILTER (WHERE fields ? rec_dist_n::text))[1],
           (array_agg((fields->>rec_dist_n::text)::numeric ORDER BY time DESC) FILTER (WHERE fields ? rec_dist_n::text))[1]
    INTO first_d, last_d FROM fit.records WHERE file_id = file AND time >= s AND time <= t;
    IF last_d IS NOT NULL THEN patch := patch || jsonb_build_object(dist_n::text, last_d - first_d); END IF;
   END IF;
   FOR stat IN
    SELECT sf.field_number AS target, rf.field_number AS source, (regexp_match(sf.field_name, '(avg|max|min)_'))[1] AS fn
    FROM fit.profile_fields sf
    JOIN fit.profile_fields rf ON rf.sdk_version = sf.sdk_version AND rf.message_number = fit.msg_num('record')
     AND rf.field_name = regexp_replace(sf.field_name, '^(enhanced_)?(avg|max|min)_', '\1')
     AND rf.scale = sf.scale AND rf."offset" = sf."offset" AND NOT rf.is_array
    WHERE sf.message_number = summary_num AND sf.field_name ~ '^(enhanced_)?(avg|max|min)_' AND NOT sf.is_array
     AND sf.sdk_version = (SELECT sdk_version FROM fit.profile_versions ORDER BY loaded_at DESC, sdk_version DESC LIMIT 1)
     AND row_.fields ? sf.field_number::text
   LOOP
    EXECUTE format('SELECT ' || CASE stat.fn WHEN 'avg' THEN 'round(avg((fields->>%1$L)::numeric))' WHEN 'max' THEN 'max((fields->>%1$L)::numeric)'
                                              ELSE 'min((fields->>%1$L)::numeric)' END ||
                   ' FROM fit.records WHERE file_id = $1 AND time >= $2 AND time <= $3 AND fields ? %1$L', stat.source::text)
     INTO value USING file, s, t;
    IF value IS NOT NULL THEN patch := patch || jsonb_build_object(stat.target::text, value); END IF;
   END LOOP;
   UPDATE fit.messages SET fields = fields || patch, time = fit.fit_time((patch->>end_n::text)::bigint)
   WHERE file_id = file AND seq = row_.seq;
  END LOOP;
 END LOOP;
END $$;

-- Section Remover: drop the records in [from_time, to_time). With close_gap, everything from to_time on moves back by the
-- length of the section; without it the gap stays and counts as paused time. Laps and sessions are recomputed.
CREATE FUNCTION fit.remove_section(src bigint, from_time timestamptz, to_time timestamptz, close_gap boolean DEFAULT true) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE new_id bigint;
BEGIN
 IF to_time <= from_time THEN RAISE EXCEPTION 'the section must end after it starts'; END IF;
 new_id := fit.copy_file(src, jsonb_build_object('operation', 'remove_section', 'from', from_time, 'to', to_time, 'close_gap', close_gap),
                         CASE WHEN close_gap THEN -extract(epoch FROM to_time - from_time)::bigint ELSE 0 END,
                         to_time, from_time, to_time);
 PERFORM fit.recompute_totals(new_id, from_time, to_time, close_gap);
 PERFORM fit.refresh_file(new_id);
 RETURN new_id;
END $$;
