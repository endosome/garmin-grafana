"""Read-only checks of the touchscreen kiosk (compose profile "kiosk"); KIOSK_URL defaults to
http://localhost:3001 and GRAFANA_PASSWORD is the admin password, which the kiosk must refuse.
Touch interaction (Back button, idle return, leaving the page) is checked by hand; see the README.
"""
import os
import requests


def main():
    base = os.getenv('KIOSK_URL', 'http://localhost:3001').rstrip('/')
    page = requests.get(base + '/', timeout=30)
    page.raise_for_status()
    if 'id="grafana"' not in page.text or 'sandbox=' not in page.text:
        raise RuntimeError('Kiosk page does not embed Grafana in a sandboxed frame')
    requests.get(base + '/grafana/api/health', timeout=30).raise_for_status()
    dashboards = requests.get(base + '/grafana/api/search', params={'type': 'dash-db'}, timeout=30)
    dashboards.raise_for_status()
    if len(dashboards.json()) != 1:
        raise RuntimeError(f'Kiosk must show exactly one dashboard, found {len(dashboards.json())}')
    saved = requests.post(base + '/grafana/api/dashboards/db', json={'dashboard': {'title': 'kiosk smoke'}, 'overwrite': True}, timeout=30)
    if saved.status_code not in (401, 403):
        raise RuntimeError(f'Anonymous kiosk viewers could save a dashboard: HTTP {saved.status_code}')
    admin = requests.get(base + '/grafana/api/admin/settings', auth=('admin', os.environ['GRAFANA_PASSWORD']), timeout=30)
    if admin.status_code == 200:
        raise RuntimeError('The kiosk accepts password logins')
    print(f'Kiosk serves "{dashboards.json()[0]["title"]}" read-only to anonymous viewers')


if __name__ == '__main__':
    main()
