-- One result per "Fetch now" request, written by the fetcher when the sync that served it ends. The button's login stays
-- INSERT-only on fetch_requests; Grafana reads the result through the view with its normal SELECT-only login.
ALTER TABLE fetch_requests ADD COLUMN finished_at timestamptz, ADD COLUMN new_points integer, ADD COLUMN result text;
CREATE VIEW fetch_status AS
 SELECT id, requested_at, finished_at, new_points, result FROM fetch_requests ORDER BY id DESC LIMIT 1;
