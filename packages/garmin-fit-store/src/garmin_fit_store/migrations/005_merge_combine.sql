-- Field Merger and File Combiner (plan item 14). Same contract as fit/004: a new file row, the originals untouched.

-- Copy the named record fields (field numbers) from the donor's records into the target's, from the donor record nearest in
-- time within the tolerance (seconds; the lower seq wins a tie). The target's record definitions gain the fields they lack,
-- with the size and base type the donor's definition gives them.
CREATE FUNCTION fit.merge_fields(target bigint, donor bigint, field_numbers integer[], tolerance numeric DEFAULT 1) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE new_id bigint; k integer; layout jsonb; added jsonb := '[]'::jsonb; d record; next_id integer; wanted text[];
BEGIN
 IF NOT EXISTS (SELECT 1 FROM fit.files WHERE id = donor) THEN RAISE EXCEPTION 'no fit.files row %', donor; END IF;
 new_id := fit.copy_file(target, jsonb_build_object('operation', 'merge_fields', 'donor', donor, 'fields', to_jsonb(field_numbers), 'tolerance', tolerance));
 wanted := ARRAY(SELECT n::text FROM unnest(field_numbers) n);
 FOREACH k IN ARRAY field_numbers LOOP
  SELECT e INTO layout FROM fit.definitions df, jsonb_array_elements(df.fields) e
   WHERE df.file_id = donor AND df.global_num = fit.msg_num('record') AND (e->>0)::int = k ORDER BY df.def_id LIMIT 1;
  IF layout IS NOT NULL THEN added := added || jsonb_build_array(layout); END IF;
  layout := NULL;
 END LOOP;
 DROP TABLE IF EXISTS merge_defs;
 CREATE TEMP TABLE merge_defs ON COMMIT DROP AS SELECT def_id AS old_def, def_id AS new_def FROM fit.definitions WHERE file_id = new_id AND false;
 SELECT coalesce(max(def_id), -1) + 1 INTO next_id FROM fit.definitions WHERE file_id = new_id;
 FOR d IN SELECT df.* FROM fit.definitions df WHERE df.file_id = new_id AND EXISTS (SELECT 1 FROM fit.records r WHERE r.file_id = new_id AND r.def_id = df.def_id) ORDER BY df.def_id LOOP
  INSERT INTO fit.definitions SELECT new_id, next_id, d.global_num, d.arch,
    (SELECT coalesce(jsonb_agg(e ORDER BY o), '[]'::jsonb) FROM (
       SELECT e, o FROM jsonb_array_elements(d.fields) WITH ORDINALITY t(e, o) WHERE NOT ((e->>0)::int = ANY (SELECT (a->>0)::int FROM jsonb_array_elements(added) a))
       UNION ALL SELECT a, 1000 + o2 FROM jsonb_array_elements(added) WITH ORDINALITY t2(a, o2)) u), d.dev_fields;
  INSERT INTO merge_defs VALUES (d.def_id, next_id);
  next_id := next_id + 1;
 END LOOP;
 DROP TABLE IF EXISTS merge_adds;
 CREATE TEMP TABLE merge_adds ON COMMIT DROP AS
  SELECT r.seq, r.time, x.add FROM fit.records r, LATERAL (
   SELECT coalesce(jsonb_object_agg(j.key, j.value), '{}'::jsonb) AS add FROM (
     SELECT dr.fields FROM fit.records dr
     WHERE dr.file_id = donor AND dr.time BETWEEN r.time - make_interval(secs => tolerance::float8) AND r.time + make_interval(secs => tolerance::float8)
     ORDER BY abs(extract(epoch FROM dr.time - r.time)), dr.seq LIMIT 1) nearest,
    LATERAL jsonb_each(nearest.fields) j WHERE j.key = ANY (wanted)) x
  WHERE r.file_id = new_id;
 UPDATE fit.records r SET def_id = m.new_def, fields = r.fields || a.add
 FROM merge_defs m, merge_adds a
 WHERE r.file_id = new_id AND r.def_id = m.old_def AND a.seq = r.seq AND a.time = r.time;
 RETURN new_id;
END $$;

-- Combine files into one, ordered by start time: the first file's file_id, every other message in time order of its file,
-- lap and session message_index renumbered, one activity message at the end, totals recomputed as in Section Remover.
CREATE FUNCTION fit.combine(file_ids bigint[]) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
 new_id bigint; first_id bigint; src record; n integer; lap_n integer := fit.msg_num('lap'); session_n integer := fit.msg_num('session');
 activity_n integer := fit.msg_num('activity'); file_id_n integer := fit.msg_num('file_id'); idx_n integer := 254;
 template record; activity_fields jsonb; ts_n integer := 253; last_time bigint; sessions integer; timer numeric; last_activity bigint;
BEGIN
 IF coalesce(array_length(file_ids, 1), 0) < 1 THEN RAISE EXCEPTION 'nothing to combine'; END IF;
 DROP TABLE IF EXISTS combine_files;
 CREATE TEMP TABLE combine_files ON COMMIT DROP AS
  SELECT id, row_number() OVER (ORDER BY start_time NULLS LAST, id) AS rank FROM fit.files WHERE id = ANY (file_ids);
 IF (SELECT count(*) FROM combine_files) <> (SELECT count(DISTINCT x) FROM unnest(file_ids) x) THEN RAISE EXCEPTION 'unknown file in %', file_ids; END IF;
 SELECT id INTO first_id FROM combine_files WHERE rank = 1;
 INSERT INTO fit.files(source_id, sha256, garmin_activity_id, parent_id, operation, protocol_version, profile_number, sdk_version, message_count)
  SELECT source_id, sha256(convert_to(file_ids::text || clock_timestamp()::text, 'UTF8')), NULL, first_id,
         jsonb_build_object('operation', 'combine', 'files', to_jsonb(ARRAY(SELECT id FROM combine_files ORDER BY rank))),
         protocol_version, profile_number, sdk_version, 0 FROM fit.files WHERE id = first_id RETURNING id INTO new_id;
 -- developer data may not collide: the same index must describe the same application in every file
 IF EXISTS (SELECT 1 FROM fit.messages a JOIN fit.messages b ON a.global_num = b.global_num AND a.file_id < b.file_id
            AND a.fields->>fit.field_num('developer_data_id', 'developer_data_index')::text = b.fields->>fit.field_num('developer_data_id', 'developer_data_index')::text
            WHERE a.global_num = fit.msg_num('developer_data_id') AND a.file_id = ANY (file_ids) AND b.file_id = ANY (file_ids) AND a.fields <> b.fields) THEN
  RAISE EXCEPTION 'the files use the same developer data index for different applications';
 END IF;
 DROP TABLE IF EXISTS combine_defs;
 CREATE TEMP TABLE combine_defs ON COMMIT DROP AS
  SELECT d.file_id AS old_file, d.def_id AS old_def, dense_rank() OVER (ORDER BY d.global_num, d.arch, d.fields::text, d.dev_fields::text) - 1 AS new_def,
         d.global_num, d.arch, d.fields, d.dev_fields
  FROM fit.definitions d WHERE d.file_id IN (SELECT id FROM combine_files);
 INSERT INTO fit.definitions SELECT DISTINCT ON (new_def) new_id, new_def, global_num, arch, fields, dev_fields FROM combine_defs ORDER BY new_def;
 DROP TABLE IF EXISTS combine_rows;
 CREATE TEMP TABLE combine_rows ON COMMIT DROP AS
  SELECT f.rank, m.seq AS old_seq, m.global_num, c.new_def, m.time, m.fields, m.dev, false AS is_record
  FROM fit.messages m JOIN combine_files f ON f.id = m.file_id JOIN combine_defs c ON c.old_file = m.file_id AND c.old_def = m.def_id
  WHERE m.global_num <> activity_n AND NOT (m.global_num = file_id_n AND f.rank > 1)
    AND NOT (f.rank > 1 AND m.global_num IN (fit.msg_num('developer_data_id'), fit.msg_num('field_description'))
             AND EXISTS (SELECT 1 FROM fit.messages p JOIN combine_files pf ON pf.id = p.file_id AND pf.rank < f.rank
                         WHERE p.global_num = m.global_num AND p.fields = m.fields))
  UNION ALL
  SELECT f.rank, r.seq, fit.msg_num('record'), c.new_def, r.time, r.fields, r.dev, true
  FROM fit.records r JOIN combine_files f ON f.id = r.file_id JOIN combine_defs c ON c.old_file = r.file_id AND c.old_def = r.def_id;
 ALTER TABLE combine_rows ADD COLUMN seq integer;
 UPDATE combine_rows c SET seq = o.n FROM (
  SELECT ctid AS id, (row_number() OVER (ORDER BY (global_num <> file_id_n), rank, old_seq) - 1)::int AS n FROM combine_rows) o WHERE c.ctid = o.id;
 INSERT INTO fit.messages SELECT new_id, seq, new_def, global_num, time, fields, dev FROM combine_rows WHERE NOT is_record;
 INSERT INTO fit.records SELECT new_id, seq, new_def, time, fields, dev FROM combine_rows WHERE is_record;
 -- lap and session message_index run on across the files
 FOREACH n IN ARRAY ARRAY[lap_n, session_n] LOOP
  UPDATE fit.messages m SET fields = jsonb_set(m.fields, ARRAY[idx_n::text], to_jsonb(r.i))
  FROM (SELECT seq, (row_number() OVER (ORDER BY seq) - 1)::int AS i FROM fit.messages WHERE file_id = new_id AND global_num = n) r
  WHERE m.file_id = new_id AND m.seq = r.seq AND m.fields ? idx_n::text;
 END LOOP;
 PERFORM fit.recompute_totals(new_id);
 -- one activity message: the last file's, brought up to date, or a new one
 SELECT count(*), coalesce(sum((fields->>fit.field_num('session', 'total_timer_time')::text)::numeric), 0), max((fields->>ts_n::text)::bigint)
  INTO sessions, timer, last_time FROM fit.messages WHERE file_id = new_id AND global_num = session_n;
 IF last_time IS NULL THEN SELECT max((fields->>ts_n::text)::bigint) INTO last_time FROM fit.records WHERE file_id = new_id; END IF;
 SELECT m.fields, m.def_id, c.new_def INTO template FROM fit.messages m JOIN combine_files f ON f.id = m.file_id
   JOIN combine_defs c ON c.old_file = m.file_id AND c.old_def = m.def_id
   WHERE m.global_num = activity_n ORDER BY f.rank DESC, m.seq DESC LIMIT 1;
 activity_fields := coalesce(template.fields, '{}'::jsonb) || jsonb_build_object(ts_n::text, last_time,
   fit.field_num('activity', 'num_sessions')::text, sessions, fit.field_num('activity', 'total_timer_time')::text, timer);
 IF template.new_def IS NULL THEN
  INSERT INTO fit.definitions SELECT new_id, coalesce(max(def_id), -1) + 1, activity_n, 0,
    jsonb_build_array(jsonb_build_array(ts_n, 4, 134), jsonb_build_array(fit.field_num('activity', 'num_sessions'), 2, 132),
                      jsonb_build_array(fit.field_num('activity', 'total_timer_time'), 4, 134)), '[]'::jsonb FROM fit.definitions WHERE file_id = new_id
    RETURNING def_id INTO n;
 ELSE
  n := template.new_def;
 END IF;
 activity_fields := (SELECT coalesce(jsonb_object_agg(key, value), '{}'::jsonb) FROM jsonb_each(activity_fields) WHERE key IN (
  SELECT e->>0 FROM fit.definitions d, jsonb_array_elements(d.fields) e WHERE d.file_id = new_id AND d.def_id = n));
 INSERT INTO fit.messages SELECT new_id, (SELECT coalesce(max(seq), -1) + 1 FROM (SELECT seq FROM fit.messages WHERE file_id = new_id UNION ALL SELECT seq FROM fit.records WHERE file_id = new_id) s),
   n, activity_n, fit.fit_time(last_time), activity_fields, '{}'::jsonb;
 PERFORM fit.refresh_file(new_id);
 RETURN new_id;
END $$;
