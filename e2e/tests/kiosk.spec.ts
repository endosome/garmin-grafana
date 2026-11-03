// Tier 4: the touchscreen kiosk (Grafana_Kiosk/index.html behind nginx), which the README used to say is checked by hand.
import AxeBuilder from '@axe-core/playwright';
import { test, expect, mainApi, sourceId } from './fixtures';

test.skip(!process.env.KIOSK_URL, 'Set KIOSK_URL to the kiosk behind nginx');
test.use({ viewport: { width: 1280, height: 800 }, hasTouch: true, baseURL: process.env.KIOSK_URL });

const IDLE_SECONDS = 20;

async function openKiosk(page: import('@playwright/test').Page, playwright: import('@playwright/test').PlaywrightTestArgs['playwright']) {
  const source = await sourceId(await mainApi(playwright), 'healthy');
  await page.goto(`/?var-Source=${source}&var-TimeZone=Europe/Budapest&idle=${IDLE_SECONDS}`);
  const frame = page.frameLocator('#grafana');
  await expect(frame.locator('[data-viz-panel-key]').first()).toBeVisible({ timeout: 60_000 });
  return { frame, grafana: () => page.frames().find(f => f.url().includes('/grafana/'))! };
}

test('the dashboard shows without errors and the Fetch now panel is not visible', async ({ page, playwright }) => {
  const { frame } = await openKiosk(page, playwright);
  // An empty state timeline says "Data does not have a time field" instead of "No data" (see KNOWN in fixtures.ts).
  await expect(frame.getByTestId('data-testid Panel data error message').filter({ hasNotText: /No data|Data does not have a time field/ })).toHaveCount(0);
  await expect(frame.locator('[data-viz-panel-key="panel-70"]')).toHaveCount(0);
  await expect(frame.locator('[data-griditem-key="grid-item-70"]:visible')).toHaveCount(0);
});

test('Back returns from a full-screen panel', async ({ page, playwright }) => {
  const { grafana } = await openKiosk(page, playwright);
  await grafana().evaluate(() => { const url = new URL(location.href); url.searchParams.set('viewPanel', 'panel-9'); location.replace(url.toString()); });
  const back = page.getByRole('button', { name: 'Back to the dashboard' });
  await expect(back).toBeVisible();
  await back.tap();
  await expect(back).toBeHidden();
  expect(grafana().url()).not.toContain('viewPanel');
});

test('the idle timeout returns to the home dashboard', async ({ page, playwright }) => {
  const { grafana } = await openKiosk(page, playwright);
  await grafana().evaluate(() => { const url = new URL(location.href); url.searchParams.set('viewPanel', 'panel-9'); location.replace(url.toString()); });
  await expect(page.getByRole('button', { name: 'Back to the dashboard' })).toBeVisible();
  await page.mouse.move(400, 400);
  await page.touchscreen.tap(400, 400);  // someone used it: only a touched dashboard is reset
  await expect.poll(() => grafana().url(), { timeout: (IDLE_SECONDS + 15) * 1000 }).not.toContain('viewPanel');
});

test('long-press menus are suppressed', async ({ page, playwright }) => {
  const { grafana } = await openKiosk(page, playwright);
  expect(await page.evaluate(() => { const e = new MouseEvent('contextmenu', { cancelable: true, bubbles: true }); document.body.dispatchEvent(e); return e.defaultPrevented; })).toBe(true);
  expect(await grafana().evaluate(() => { const e = new MouseEvent('contextmenu', { cancelable: true, bubbles: true }); document.body.dispatchEvent(e); return e.defaultPrevented; })).toBe(true);
});

test('links that would leave the dashboard bring it back', async ({ page, playwright }) => {
  const { grafana } = await openKiosk(page, playwright);
  await grafana().evaluate(() => location.replace('/grafana/profile'));  // without ?kiosk: the page notices and goes home
  await expect.poll(() => grafana().url(), { timeout: 15_000 }).toContain('kiosk');
  await expect.poll(() => grafana().url()).not.toContain('/profile');
});

test('the kiosk page has no accessibility violations', async ({ page, playwright }) => {
  await openKiosk(page, playwright);
  const results = await new AxeBuilder({ page })
    .exclude('#grafana')  // our page; Grafana's own UI is out of scope
    .disableRules(['meta-viewport'])  // zooming is off on purpose: a fixed touchscreen, where a pinch would break the layout
    .analyze();
  expect(results.violations.map(v => `${v.id}: ${v.help}`)).toEqual([]);
});
