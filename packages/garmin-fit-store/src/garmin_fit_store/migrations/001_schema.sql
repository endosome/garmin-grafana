-- The fit schema (plan R9 to R13). References nothing in public; source_id has no foreign key on purpose.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE SCHEMA IF NOT EXISTS fit;

CREATE TABLE fit.profile_versions (
 sdk_version text PRIMARY KEY, profile_version text NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE fit.profile_types (
 sdk_version text NOT NULL REFERENCES fit.profile_versions, type_name text NOT NULL,
 raw_value bigint NOT NULL, value_name text NOT NULL,
 PRIMARY KEY (sdk_version, type_name, raw_value)
);
CREATE TABLE fit.profile_messages (
 sdk_version text NOT NULL REFERENCES fit.profile_versions, message_number integer NOT NULL, message_name text NOT NULL,
 PRIMARY KEY (sdk_version, message_number)
);
CREATE TABLE fit.profile_fields (
 sdk_version text NOT NULL REFERENCES fit.profile_versions, message_number integer NOT NULL, field_number integer NOT NULL,
 field_name text NOT NULL, field_type text NOT NULL, base_type text NOT NULL, is_array boolean NOT NULL,
 scale double precision NOT NULL, "offset" double precision NOT NULL, units text NOT NULL,
 definition jsonb NOT NULL,
 PRIMARY KEY (sdk_version, message_number, field_number)
);

CREATE TABLE fit.files (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 source_id bigint,
 sha256 bytea NOT NULL,
 garmin_activity_id bigint,
 parent_id bigint REFERENCES fit.files(id) ON DELETE SET NULL,
 operation jsonb,
 protocol_version smallint NOT NULL, profile_number integer NOT NULL,
 sdk_version text NOT NULL, message_count integer NOT NULL,
 start_time timestamptz, end_time timestamptz,
 imported_at timestamptz NOT NULL DEFAULT now()
);
-- One original per source; NULL source ids (single-user, browser) compare equal.
CREATE UNIQUE INDEX files_sha256 ON fit.files (COALESCE(source_id, 0), sha256) WHERE parent_id IS NULL;
CREATE INDEX files_activity ON fit.files (source_id, garmin_activity_id);
CREATE INDEX files_parent ON fit.files (parent_id);

CREATE TABLE fit.blobs (
 file_id bigint PRIMARY KEY REFERENCES fit.files(id) ON DELETE CASCADE,
 data bytea NOT NULL
);
CREATE TABLE fit.definitions (
 file_id bigint NOT NULL REFERENCES fit.files(id) ON DELETE CASCADE,
 def_id integer NOT NULL, global_num integer NOT NULL, arch smallint NOT NULL,
 fields jsonb NOT NULL, dev_fields jsonb NOT NULL,
 PRIMARY KEY (file_id, def_id)
);
CREATE TABLE fit.messages (
 file_id bigint NOT NULL REFERENCES fit.files(id) ON DELETE CASCADE,
 seq integer NOT NULL, def_id integer NOT NULL, global_num integer NOT NULL,
 time timestamptz, fields jsonb NOT NULL, dev jsonb NOT NULL,
 PRIMARY KEY (file_id, seq)
);
CREATE INDEX messages_global ON fit.messages (file_id, global_num);
CREATE TABLE fit.records (
 file_id bigint NOT NULL, seq integer NOT NULL, def_id integer NOT NULL,
 time timestamptz NOT NULL, fields jsonb NOT NULL, dev jsonb NOT NULL,
 PRIMARY KEY (file_id, time, seq)
);
SELECT create_hypertable('fit.records', 'time', chunk_time_interval => INTERVAL '30 days');
CREATE INDEX records_seq ON fit.records (file_id, seq);
