// Optional browser smoke test. Uses the committed read-only demo, never Xyce.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require = process.env.OPENYIELD_PLAYWRIGHT_PACKAGE
  ? createRequire(pathToFileURL(process.env.OPENYIELD_PLAYWRIGHT_PACKAGE))
  : createRequire(import.meta.url);
const { chromium } = require('playwright');
const base = process.env.OPENYIELD_TEST_URL || 'http://127.0.0.1:8767';
const options = { headless: true };
if (process.env.OPENYIELD_BROWSER_EXECUTABLE) options.executablePath = process.env.OPENYIELD_BROWSER_EXECUTABLE;
const browser = await chromium.launch(options);
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  const context = await (await page.request.get(base + '/api/context')).json();
  assert.equal(context.demo, true, 'Run this smoke test against server.py --demo only');
  await page.waitForFunction(() => document.querySelectorAll('#job-list tbody tr').length > 0);
  await page.locator('.sidebar [data-view="evidence"]').click();
  await page.locator('#scope').selectOption(context.jobs[0].id);
  await page.locator('#evidence-content').getByRole('button', { name: '查看证据' }).first().click();
  await page.locator('#detail-content[aria-busy]').waitFor({ state: 'detached' });
  await page.locator('#tab-samples').click();
  await page.locator('#detail-content[aria-busy]').waitFor({ state: 'detached' });
  assert.ok(await page.locator('#detail-content .sample-table tbody tr').count() > 0);
  const before = context.jobs.length;
  const denied = await page.request.post(base + '/api/run', {
    headers: { Origin: base, 'X-OpenYield-Token': context.token }, data: { config: context.template }
  });
  assert.equal(denied.status(), 400);
  assert.equal((await (await page.request.get(base + '/api/context')).json()).jobs.length, before);
  assert.deepEqual(errors, []);
  console.log('Demo browser smoke passed: history, evidence, samples, read-only rejection; no tasks launched.');
} finally {
  await browser.close();
}
