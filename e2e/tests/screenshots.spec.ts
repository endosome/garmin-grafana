// Tier 5: chart panels against reviewed baselines (thresholds, bands, stacking, heatmaps, plugin rendering).
// One persona per dashboard over one range, rather than every persona times every range. Baselines are regenerated only by
// update-baselines.sh, inside the pinned container; see docs/testing.md.
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

// Chunks of panels, so that the tests can be spread over workers and CI shards: a dashboard of 50 charts in one test takes minutes.
const CHUNK = 12;

for (const [name, persona] of PLAN) {
  const all = panelsOf(name);
  for (let first = 0; first < all.length; first += CHUNK) {
    const chunk = all.slice(first, first + CHUNK);
    test(`screenshots of the chart panels of ${DASHBOARDS[name].file}, ${first / CHUNK + 1} of ${Math.ceil(all.length / CHUNK)}`, async ({ page, request }) => {
      test.setTimeout(300_000);
      await openDashboard(page, request, name, { persona });
      for (const panel of chunk) {
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
}
