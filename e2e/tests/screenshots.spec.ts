// Tier 5: chart panels against reviewed baselines (thresholds, bands, stacking, heatmaps, plugin rendering).
// One persona per dashboard over one range, rather than every persona times every range. Baselines are regenerated only by
// update-baselines.sh, inside the pinned container; see docs/dashboard-testing-plan.md.
import fs from 'node:fs';
import path from 'node:path';
import { DASHBOARDS, openDashboard, reveal, test, expect, type DashboardName, type PersonaName } from './fixtures';

const CHARTS = new Set(['timeseries', 'barchart', 'piechart', 'state-timeline', 'status-history', 'heatmap', 'histogram', 'trend', 'xychart', 'bargauge', 'gauge', 'marcusolsson-hourly-heatmap-panel']);
// Geomaps are left out: their basemap comes from the internet, which these tests block.
const PLAN: [DashboardName, PersonaName][] = [['stats', 'healthy'], ['health', 'healthy'], ['ski', 'ski'], ['strength', 'gym']];

function panelsOf(name: DashboardName): { id: number; title: string; type: string }[] {
  const file = path.resolve(__dirname, '../../Grafana_Dashboard', DASHBOARDS[name].file);
  const found: { id: number; title: string; type: string }[] = [];
  const walk = (items: any[]) => items.forEach(panel => { if (CHARTS.has(panel.type)) found.push({ id: panel.id, title: panel.title ?? '', type: panel.type }); walk(panel.panels ?? []); });
  walk(JSON.parse(fs.readFileSync(file, 'utf8')).panels);
  return found;
}

for (const [name, persona] of PLAN) {
  test(`screenshots of the chart panels of ${DASHBOARDS[name].file}`, async ({ page, request }) => {
    test.setTimeout(600_000);
    await openDashboard(page, request, name, { persona });
    for (const panel of panelsOf(name)) {
      const element = page.locator(`[data-viz-panel-key="panel-${panel.id}"]`).first();
      if (!(await reveal(page, panel.id))) {
        expect.soft(false, `${panel.title} (${panel.id}) is not on the page`).toBe(true);
        continue;
      }
      await page.waitForTimeout(1500);  // virtualized panels draw after they scroll into view
      await expect.soft(element, `${panel.title} (${panel.id})`).toHaveScreenshot(`${name}-${panel.id}.png`, { timeout: 20_000 });
    }
  });
}
