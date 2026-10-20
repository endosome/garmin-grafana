# Kubernetes / Helm

The chart and standalone `../kubernetes-spec-example.yaml` run PostgreSQL 17 with
TimescaleDB 2.26.4 Apache-only, ingestion, and Grafana 13.0.2. No operator or
external chart dependency is required. PVCs persist PostgreSQL, tokens, and
Grafana, and carry Helm's keep annotation. Existing Influx PVCs are never reused
or removed. Deploy under a separate release/namespace during migration.

Build and push the new image to a registry reachable by your cluster:

```bash
docker build -t registry.example.com/garmin-grafana:timescale .
docker push registry.example.com/garmin-grafana:timescale
kubectl create namespace garmin
# Store these values in a local file with mode 600; do not commit it:
# PGPASSWORD, PGREADER_PASSWORD, GF_SECURITY_ADMIN_PASSWORD,
# GARMINCONNECT_EMAIL, GARMINCONNECT_BASE64_PASSWORD
kubectl -n garmin create secret generic garmin-credentials --from-env-file=/secure/garmin.env
helm upgrade --install garmin ./k8s -n garmin \
  --set appImage=registry.example.com/garmin-grafana:timescale \
  --set timezone=Europe/London --set account=default
kubectl -n garmin port-forward service/garmin-grafana 3000:3000
```

`PGREADER_PASSWORD` initializes the SELECT-only grafana_reader database login.
All database migrations execute in an ingestion init container after PostgreSQL
is ready. For MFA-enabled accounts, authenticate using the Compose interactive
path or a local process first, then securely transfer the token directory into
the ingestion token PVC before starting normal ingestion. Non-interactive pods
cannot answer an MFA prompt. No credentials are stored in Helm values.

Choose a storage class and capacities in values.yaml. The default ingress is
ClusterIP only; configure your own authenticated TLS ingress. PostgreSQL's
initialization image determines its container UID. The database Deployment uses
Recreate to avoid simultaneous writers to a single PVC; it is not an HA setup.
A single app replica owns synchronization. Back up before schema/major database
upgrades; follow the main README and migration guide.

`make -C k8s render lint` synchronizes dashboard assets, renders the standalone
example, and lints the chart. For the standalone manifest, replace the app image
with your published image and create the same Secret before `kubectl apply`.
The resources are rendered and linted in development; no live Kubernetes cluster
has been used for acceptance testing. Verify storage permissions, MFA/token
bootstrap, startup, backups and PVC recovery on your cluster before relying on it.
