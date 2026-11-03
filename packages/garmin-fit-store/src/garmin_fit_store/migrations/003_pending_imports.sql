-- Files the collector could not store (retried on its next run); a failure here never fails the activity's own processing.
CREATE TABLE fit.pending_imports (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 source_id bigint, garmin_activity_id bigint, data bytea NOT NULL,
 error text NOT NULL, attempts integer NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(), last_attempt_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX pending_imports_file ON fit.pending_imports (COALESCE(source_id, 0), sha256(data));
