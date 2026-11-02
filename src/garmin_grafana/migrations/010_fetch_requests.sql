-- Grafana's "Fetch now" button adds a row through an INSERT-only role. Every fetcher compares the newest id with
-- the one it saw when its last sync began, so a request made during a sync starts another one right after it.
CREATE TABLE fetch_requests (id bigserial PRIMARY KEY, requested_at timestamptz NOT NULL DEFAULT now());
