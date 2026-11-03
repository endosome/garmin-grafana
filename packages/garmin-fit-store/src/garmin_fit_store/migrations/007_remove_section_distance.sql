-- Section Remover with close_gap also clamps laps and sessions in the original time (after the shift a time inside the section
-- can't be told from one shifted into it) and takes the removed stretch out of the cumulative record distance: the records after the
-- section continue from the last record before it, so laps and the session no longer count distance covered in the removed part.
CREATE OR REPLACE FUNCTION fit.remove_section(src bigint, from_time timestamptz, to_time timestamptz, close_gap boolean DEFAULT true) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE new_id bigint; summary text; row_ record; s timestamptz; t timestamptz; length bigint := extract(epoch FROM to_time - from_time)::bigint;
 dist text := fit.field_num('record', 'distance')::text; before_d numeric; after_d numeric;
BEGIN
 IF to_time <= from_time THEN RAISE EXCEPTION 'the section must end after it starts'; END IF;
 new_id := fit.copy_file(src, jsonb_build_object('operation', 'remove_section', 'from', from_time, 'to', to_time, 'close_gap', close_gap),
                         CASE WHEN close_gap THEN -extract(epoch FROM to_time - from_time)::bigint ELSE 0 END,
                         to_time, from_time, to_time);
 IF close_gap THEN
  SELECT (fields->>dist)::numeric INTO before_d FROM fit.records
   WHERE file_id = new_id AND time < from_time AND jsonb_typeof(fields->dist) = 'number' ORDER BY time DESC, seq DESC LIMIT 1;
  SELECT (fields->>dist)::numeric INTO after_d FROM fit.records
   WHERE file_id = new_id AND time >= from_time AND jsonb_typeof(fields->dist) = 'number' ORDER BY time, seq LIMIT 1;
  FOREACH summary IN ARRAY ARRAY['lap', 'session'] LOOP
   FOR row_ IN SELECT * FROM fit.messages WHERE file_id = src AND global_num = fit.msg_num(summary)
               AND fields ? fit.field_num(summary, 'start_time')::text AND fields ? fit.field_num(summary, 'timestamp')::text LOOP
    s := fit.fit_time((row_.fields->>fit.field_num(summary, 'start_time')::text)::bigint);
    t := fit.fit_time((row_.fields->>fit.field_num(summary, 'timestamp')::text)::bigint);
    IF s >= from_time AND s < to_time THEN s := from_time; END IF;
    IF t > from_time AND t < to_time THEN t := from_time; END IF;
    IF t < s THEN t := s; END IF;
    IF s >= to_time THEN s := s - make_interval(secs => length); END IF;
    IF t >= to_time THEN t := t - make_interval(secs => length); END IF;
    UPDATE fit.messages SET time = t, fields = fields || jsonb_build_object(fit.field_num(summary, 'start_time')::text, extract(epoch FROM s)::bigint - 631065600,
                                                                              fit.field_num(summary, 'timestamp')::text, extract(epoch FROM t)::bigint - 631065600)
     WHERE file_id = new_id AND seq = row_.seq;
   END LOOP;
  END LOOP;
  IF before_d IS NOT NULL AND after_d IS NOT NULL AND after_d > before_d THEN
   UPDATE fit.records SET fields = jsonb_set(fields, ARRAY[dist], to_jsonb((fields->>dist)::numeric - (after_d - before_d)))
    WHERE file_id = new_id AND time >= from_time AND jsonb_typeof(fields->dist) = 'number';
  END IF;
 END IF;
 PERFORM fit.recompute_totals(new_id, CASE WHEN close_gap THEN NULL ELSE from_time END, CASE WHEN close_gap THEN NULL ELSE to_time END, close_gap);
 PERFORM fit.refresh_file(new_id);
 RETURN new_id;
END $$;
