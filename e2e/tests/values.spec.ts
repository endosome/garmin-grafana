// Tier 2: the text Grafana renders for tiles, in the unit and format the dashboards ask for (clocks, decimals, suffixes, noValue).
// Each string was read off the rendered panel and checked by eye against the value tests/personas.py computes for the persona.
import { openDashboard, panelText, reveal, test, expect, type DashboardName, type PersonaName } from './fixtures';

const EXPECTED: [DashboardName, PersonaName, Record<number, string>][] = [
  ['health', 'healthy', {
    1: 'Sleep score82', 2: 'Sleep7h:08m', 3: 'HRV40 ms', 4: 'Resting HR54 bpm', 5: 'Battery at wake88', 6: 'Stress today22', 7: 'Steps today3,630',
    8: 'Intensity, 7 days340', 42: 'VO2 max53',
  }],
  ['stats', 'healthy', {
    28: 'Today RHR54Resting HR', 30: "Today's Steps3.63 KSteps 👟", 31: 'Today SpO295SpO2 %', 26: 'Newest data from the watch5 hours ago', 71: 'Last Fetcha few seconds ago',
  }],
  ['ski', 'ski', {
    1: 'Ski season34 days to go', 2: 'Vertical, 7 days4625 m', 3: 'Vertical / week, 4 weeks3626 m', 4: 'Uphill time, 7 days13h:45m',
    5: 'Longest uphill, 4 weeks6h:26m', 6: 'Strength, 14 days4', 7: 'Load ramp1.27',
  }],
  ['strength', 'gym', {
    1002: 'Total Volume81930.0 kg', 1003: 'Weighted Sets207', 1004: 'Total Reps1.64 K', 1005: 'Number of Workouts37', 1006: 'Avg Volume / Workout2214.3 kg',
    1022: 'Sets Needing Classification (UNKNOWN)36',
  }],
  // Missing values are an en dash, never a zero or a blank.
  ['health', 'fitbit', { 1: 'Sleep score–', 2: 'Sleep–', 3: 'HRV–', 4: 'Resting HR–', 5: 'Battery at wake–', 7: 'Steps today–' }],
  ['health', 'newcomer', { 6: 'Stress today–' }],
];

for (const [name, persona, tiles] of EXPECTED) {
  test(`${name} tiles for ${persona}`, async ({ page, request }) => {
    await openDashboard(page, request, name, { persona });
    for (const [id, text] of Object.entries(tiles)) expect(await panelText(page, Number(id)), `panel ${id}`).toBe(text);
  });
}

test('durations in a table use dthms and bar gauges scale seconds', async ({ page, request }) => {
  await openDashboard(page, request, 'health', { persona: 'healthy' });
  await reveal(page, 28);
  await page.waitForTimeout(1500);
  expect(await panelText(page, 28)).toContain('Running3834:13:24353 km146');
  await reveal(page, 11);
  await page.waitForTimeout(1500);
  expect(await panelText(page, 11)).toContain('Deep1.4 hoursLight4.1 hoursREM1.6 hoursAwake13.0 mins');
});
