"""Read-only Grafana provisioning and datasource smoke test; requires GRAFANA_URL,
GRAFANA_USER and GRAFANA_PASSWORD. Uses synthetic macro values, never Garmin login.
"""
import os
import requests
from validate_dashboard_queries import dashboard_queries, expand_query


def main():
    base=os.getenv('GRAFANA_URL','http://localhost:3000').rstrip('/')
    session=requests.Session()
    session.auth=(os.getenv('GRAFANA_USER','admin'),os.environ['GRAFANA_PASSWORD'])
    response=session.get(base+'/api/datasources/uid/garmin_postgres/health',timeout=30)
    response.raise_for_status()
    if response.json().get('status')!='OK':
        raise RuntimeError('Grafana datasource health failed')
    response=session.get(base+'/api/search',params={'type':'dash-db'},timeout=30)
    response.raise_for_status()
    if len(response.json())<2:
        raise RuntimeError('Expected both provisioned dashboards')
    count=0
    for label,query,fmt in dashboard_queries():
        response=session.post(base+'/api/ds/query',json={
            'from':'1577836800000','to':'1893456000000',
            'queries':[{'refId':'A','datasource':{'uid':'garmin_postgres','type':'grafana-postgresql-datasource'},
                        'rawSql':expand_query(query),'format':'table' if fmt=='variable' else fmt}]},timeout=60)
        if not response.ok:
            raise RuntimeError(f'Grafana query failed: {label}: {response.text}')
        result=response.json().get('results',{}).get('A',{})
        if result.get('error') or result.get('status',200)>=400:
            raise RuntimeError(f'Grafana query failed: {label}: {result.get("error")}')
        count+=1
    print(f'Grafana datasource healthy; two dashboards provisioned; {count} queries succeeded through Grafana')
    session.close()


if __name__=='__main__':
    main()
