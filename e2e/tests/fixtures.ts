import { expect, test as base, type Page, type APIRequestContext, type PlaywrightTestArgs } from '@playwright/test';

/** "Now" of the personas (tests/personas.py): 2026-10-28 12:00 in Europe/Budapest, which is UTC+1 after the DST change. */
export const NOW = Date.UTC(2026, 9, 28, 11, 0, 0);
export const DAY = 86_400_000;

export const DASHBOARDS = {
  stats: { uid: 'feiibsx498gsgb', file: 'Garmin-Grafana-Dashboard.json', zone: 'Europe/Budapest', range: 30 },
  health: { uid: 'garmin-health-overview', file: 'Garmin-Health-Overview.json', zone: 'Europe/Budapest', range: 90 },
  ski: { uid: 'garmin-ski-season', file: 'Garmin-Ski-Season.json', zone: 'Europe/Budapest', range: 120 },
  strength: { uid: 'garmin-strength-training', file: 'Garmin-Strength-Training-Dashboard.json', zone: 'UTC', range: 180 },
} as const;
export type DashboardName = keyof typeof DASHBOARDS;

export const PERSONAS = {
  healthy: 'persona-healthy-lifestyle',
  ski: 'persona-ski-tourer',
  gym: 'persona-gym-goer',
  newcomer: 'persona-newcomer',
  fitbit: 'persona-public-fitbit',
} as const;
export type PersonaName = keyof typeof PERSONAS;

const NO_ANIMATION = `*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }`;

/** The source id Grafana's Source variable uses for a persona's account (the newest account of that name). */
export async function sourceId(request: APIRequestContext, persona: PersonaName): Promise<number> {
  const response = await request.post('/api/ds/query', {
    data: { queries: [{ refId: 'A', datasource: { uid: 'garmin_postgres', type: 'grafana-postgresql-datasource' }, format: 'table',
      rawSql: `SELECT id FROM sources WHERE account LIKE '${PERSONAS[persona]}%' ORDER BY id DESC LIMIT 1` }] },
  });
  const frames = (await response.json()).results.A.frames;
  return frames[0].data.values[0][0];
}

export interface Problems { errors: string[]; console: string[]; failedQueries: string[]; failedRequests: string[]; }

export interface OpenOptions {
  persona?: PersonaName;
  /** Days before NOW the range starts; the dashboard's own default when omitted. */
  days?: number;
  to?: number;
  vars?: Record<string, string>;
  /** Where to start from, for the kiosk, whose Grafana lives under /grafana. */
  prefix?: string;
}

export const test = base.extend<{ problems: Problems }>({
  problems: [async ({ page }, use) => {
    const problems: Problems = { errors: [], console: [], failedQueries: [], failedRequests: [] };
    // Nothing outside the stack: map tiles, grafana.com news, avatars. Blocked, so a test never waits for the internet.
    const origin = new URL(process.env.GRAFANA_URL ?? 'http://localhost:3000').origin;
    const kiosk = process.env.KIOSK_URL ? new URL(process.env.KIOSK_URL).origin : origin;
    await page.route(url => ![origin, kiosk].includes(url.origin) && !url.protocol.startsWith('data') && !url.protocol.startsWith('blob'), route => route.abort());
    page.on('console', message => {
      if (message.type() === 'error' && !/Failed to load resource.*(ERR_FAILED|ERR_BLOCKED)/.test(message.text())) problems.console.push(message.text());
    });
    page.on('pageerror', error => problems.console.push(String(error)));
    page.on('response', async response => {
      const url = response.url();
      if (!url.includes('/api/ds/query')) {
        // A plugin whose files fail to load is "plugin not found" on every panel that uses it.
        if (response.status() >= 400 && new URL(url).origin === origin && !/favicon|\/api\/(frontend-metrics|live)|\/avatar\//.test(url)) problems.failedRequests.push(`${response.status()} ${url}`);
        return;
      }
      if (!response.ok()) { problems.failedQueries.push(`${response.status()} ${url}`); return; }
      // Grafana answers 200 and puts a failed query's message in the result; panels scrolled out of view forget their error icon.
      const results = (await response.json().catch(() => ({ results: {} }))).results ?? {};
      for (const [refId, result] of Object.entries<any>(results)) if (result.error) problems.errors.push(`${refId}: ${result.error}`);
    });
    await use(problems);
  }, { auto: true }],
});
export { expect };

export async function openDashboard(page: Page, request: APIRequestContext, name: DashboardName, options: OpenOptions = {}) {
  const dashboard = DASHBOARDS[name];
  const source = await sourceId(request, options.persona ?? 'healthy');
  const to = options.to ?? NOW;
  const from = to - (options.days ?? dashboard.range) * DAY;
  const params = new URLSearchParams({
    orgId: '1', 'var-Source': String(source), 'var-TimeZone': dashboard.zone, from: String(from), to: String(to), timezone: dashboard.zone, refresh: '',
    ...options.vars,
  });
  // Grafana computes "now", pinned ranges (24h tiles) and "today" in the browser, then sends absolute times to the server.
  await page.clock.setFixedTime(new Date(NOW));
  await page.addInitScript(css => { document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('style'); s.textContent = css; document.head.append(s); }); }, NO_ANIMATION);
  await page.goto(`${options.prefix ?? ''}/d/${dashboard.uid}?${params}`);
  return { source, panelCount: await settle(page) };
}


/** Moves the dashboard's own scroll container (not the docked navigation menu, which scrolls too): to the top, or down a step. */
export function scrollDashboard(page: Page, to: number | null): Promise<boolean> {
  return page.evaluate(top => {
    let el: HTMLElement | null = document.querySelector('[data-viz-panel-key]');
    while (el && !(el.scrollHeight > el.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(el).overflowY))) el = el.parentElement;
    if (!el) return false;
    const before = el.scrollTop;
    el.scrollTop = top === null ? el.scrollTop + 500 : top;
    return top !== null || el.scrollTop !== before;
  }, to);
}

/** Expands collapsed rows and scrolls every panel into view (Grafana loads panels lazily), then waits for the queries to finish. */
export async function settle(page: Page): Promise<number> {
  await page.getByTestId('data-testid dashboard controls').waitFor();
  let inFlight = 0;
  const started = () => { inFlight++; };
  const finished = () => { inFlight = Math.max(0, inFlight - 1); };
  const watch = (request: { url(): string }) => request.url().includes('/api/ds/query');
  page.on('request', request => watch(request) && started());
  page.on('requestfinished', request => watch(request) && finished());
  page.on('requestfailed', request => watch(request) && finished());
  // A collapsed row's button is labelled "Expand row ..."; opening one moves the rows below it, so look again each time.
  for (let opened = 0; opened < 40; opened++) {
    const collapsed = page.locator('[data-testid^="data-testid dashboard-row-toggle-for-"][aria-label^="Expand row"]');
    if (!(await collapsed.count())) break;
    await collapsed.first().click();
    await page.waitForTimeout(200);
  }
  await page.locator('[data-viz-panel-key]').first().waitFor();
  for (let step = 0; step < 80; step++) {
    const moved = await scrollDashboard(page, null);
    await page.waitForTimeout(250);
    if (!moved) break;
  }
  await scrollDashboard(page, 0);
  await expect.poll(() => inFlight, { timeout: 60_000, intervals: [500] }).toBe(0);
  await page.waitForTimeout(1000);
  await expect.poll(() => inFlight, { timeout: 30_000, intervals: [500] }).toBe(0);
  return page.locator('[data-testid^="data-testid Panel header"]').count();
}

export const panelHeader = (page: Page, title: string) => page.getByTestId(`data-testid Panel header ${title}`);

/** The text a panel shows, whitespace collapsed; stat tiles come out as "TitleValue unit". */
export async function panelText(page: Page, id: number): Promise<string> {
  const text = await page.locator(`[data-viz-panel-key="panel-${id}"]`).first().textContent();
  return (text ?? '').replace(/\s+/g, ' ').trim();
}

// A state timeline with no rows at all says this instead of "No data", whatever the query's format; it is Grafana's wording, not a broken panel.
const KNOWN = ['panel-10: Data does not have a time field', 'panel-36: Data does not have a time field'];  // Health: sleep stages, lifestyle journal

/** What a dashboard shows wrong, as a list that is empty when it is fine. */
export async function whatIsWrong(page: Page, problems: Problems): Promise<Record<string, string[]>> {
  // "No data" is how Grafana words an empty result in the same element as a real error: an empty persona has plenty of them.
  const visible = (await page.getByTestId('data-testid Panel data error message').evaluateAll(els => els.map(e => `${e.closest('[data-viz-panel-key]')?.getAttribute('data-viz-panel-key')}: ${e.textContent}`))).filter(text => !/: No data$/.test(text) && !KNOWN.includes(text));
  const notFound = await page.getByText(/plugin not found/i).allTextContents();
  const wrong = { 'panel errors': [...problems.errors, ...visible], 'plugin not found': notFound, console: problems.console, 'failed queries': problems.failedQueries, 'failed requests': problems.failedRequests };
  return Object.fromEntries(Object.entries(wrong).filter(([, list]) => list.length));
}

/** The main Grafana's API with the admin login, for tests whose page lives elsewhere (the kiosk). */
export const mainApi = (playwright: PlaywrightTestArgs['playwright']) => playwright.request.newContext({
  baseURL: (process.env.GRAFANA_URL ?? 'http://localhost:3000').replace(/\/$/, ''),
  extraHTTPHeaders: { Authorization: `Basic ${Buffer.from(`${process.env.GRAFANA_USER ?? 'admin'}:${process.env.GRAFANA_PASSWORD ?? ''}`).toString('base64')}` },
});

/** Scrolls the dashboard in steps from the top until a panel is on the page (Grafana only renders panels near the viewport). */
export async function reveal(page: Page, id: number): Promise<boolean> {
  const panel = page.locator(`[data-viz-panel-key="panel-${id}"]`);
  await scrollDashboard(page, 0);
  for (let step = 0; step < 80; step++) {
    await page.waitForTimeout(200);
    if (await panel.count()) { await panel.first().scrollIntoViewIfNeeded(); return true; }
    if (!(await scrollDashboard(page, null))) break;
  }
  return (await panel.count()) > 0;
}
