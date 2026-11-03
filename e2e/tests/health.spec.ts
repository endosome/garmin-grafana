// Tier 1: every dashboard loads for the personas it has data for, without a panel error, a missing plugin or a console error.
import { DASHBOARDS, openDashboard, test, expect, whatIsWrong, type DashboardName, type PersonaName } from './fixtures';

const COMBINATIONS: [DashboardName, PersonaName][] = [
  ['stats', 'healthy'], ['health', 'healthy'], ['health', 'newcomer'], ['health', 'fitbit'], ['ski', 'ski'], ['ski', 'newcomer'], ['strength', 'gym'], ['strength', 'newcomer'],
];

for (const [name, persona] of COMBINATIONS) {
  test(`${DASHBOARDS[name].file} loads for ${persona} without errors`, async ({ page, request, problems }) => {
    const { panelCount } = await openDashboard(page, request, name, { persona });
    expect(panelCount).toBeGreaterThan(5);
    expect(await whatIsWrong(page, problems)).toEqual({});
  });
}

test('the health dashboard shows data where the healthy persona has it', async ({ page, request }) => {
  await openDashboard(page, request, 'health', { persona: 'healthy' });
  for (const id of [1, 2, 3, 4, 5, 7]) {
    await expect(page.locator(`[data-viz-panel-key="panel-${id}"]`)).not.toContainText(/No data|^–$/);
  }
});
