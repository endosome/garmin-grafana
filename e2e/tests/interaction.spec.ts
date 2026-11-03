// Tier 3: dropdowns, links, collapsed rows and the Fetch now button.
import { DASHBOARDS, NOW, PERSONAS, openDashboard, panelText, test, expect } from './fixtures';

test('the Source dropdown lists the persona accounts and choosing one updates the panels', async ({ page, request }) => {
  await openDashboard(page, request, 'health', { persona: 'healthy' });
  expect(await panelText(page, 7)).toBe('Steps today3,630');
  const dropdown = page.getByTestId('data-testid template variable').first();
  await dropdown.click();
  const options = await page.getByRole('option').allTextContents();
  for (const account of Object.values(PERSONAS)) expect(options.some(o => o.includes(account)), account).toBe(true);
  await page.getByRole('option', { name: new RegExp(PERSONAS.newcomer) }).first().click();
  await page.keyboard.press('Escape');
  await expect.poll(() => panelText(page, 7), { timeout: 30_000 }).toBe('Steps today10,000');
});

test('dashboard links keep the time range and the variables', async ({ page, request }) => {
  const { source } = await openDashboard(page, request, 'stats', { persona: 'healthy' });
  await page.getByRole('link', { name: 'Garmin Ski Season' }).click();
  await page.waitForURL(new RegExp(`/d/${DASHBOARDS.ski.uid}`));
  const url = new URL(page.url());
  expect(url.searchParams.get('var-Source')).toBe(String(source));
  expect(url.searchParams.get('var-TimeZone')).toBe('Europe/Budapest');
  const instant = (value: string | null) => (/^\d+$/.test(value ?? '') ? Number(value) : Date.parse(value ?? ''));  // epoch ms, or the ISO text Grafana writes for a link
  expect(instant(url.searchParams.get('to'))).toBe(NOW);
  expect(instant(url.searchParams.get('from'))).toBe(NOW - DASHBOARDS.stats.range * 86_400_000);
});

test('a collapsed row opens and its panels load', async ({ page, request }) => {
  await openDashboard(page, request, 'health', { persona: 'healthy' });
  await page.goto(page.url());  // a fresh load: rows start as the file has them
  await page.getByTestId('data-testid dashboard controls').waitFor();
  await expect(page.locator('[data-viz-panel-key="panel-38"]')).toHaveCount(0);  // inside the collapsed "Last 24 hours" row
  const toggle = page.getByTestId('data-testid dashboard-row-toggle-for-Last 24 hours');
  await toggle.scrollIntoViewIfNeeded();
  await toggle.click();
  await expect(page.locator('[data-viz-panel-key="panel-38"]')).toBeVisible();
});

test.describe('Fetch now', () => {
  test.skip(process.env.GRAFANA_ALLOW_WRITES !== '1', 'inserts a fetch request: set GRAFANA_ALLOW_WRITES=1 on a disposable stack without garmin-fetch-data');

  test('inserts one fetch request and shows the toast', async ({ page, request }) => {
    const count = async () => {
      const response = await request.post('/api/ds/query', { data: { queries: [{ refId: 'A', datasource: { uid: 'garmin_postgres', type: 'grafana-postgresql-datasource' }, format: 'table', rawSql: 'SELECT count(*) FROM fetch_requests' }] } });
      return Number((await response.json()).results.A.frames[0].data.values[0][0]);
    };
    await openDashboard(page, request, 'stats', { persona: 'healthy' });
    const before = await count();
    await page.locator('[data-viz-panel-key="panel-70"]').getByRole('button', { name: 'Fetch now' }).click();
    await expect(page.getByText('Fetch new data from Garmin Connect now?')).toBeVisible();
    await page.getByRole('dialog').getByRole('button', { name: 'Fetch now' }).click();
    await expect(page.getByText(/Newest data from the watch only changes after the watch uploads/)).toBeVisible();
    await expect.poll(count).toBe(before + 1);
  });
});

