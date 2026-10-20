.PHONY: render lint sync
sync:
	cp ../Grafana_Dashboard/*.json files/
	cp ../Grafana_Datasource/postgres.yaml files/
render: sync
	helm template garmin . | sed 's/[[:space:]]*$$//' > ../kubernetes-spec-example.yaml
lint: sync
	helm lint .
