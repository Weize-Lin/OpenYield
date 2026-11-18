import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(new URL('../../openyield-bench/package.json', import.meta.url));
const { chromium } = require('playwright');
const root = fileURLToPath(new URL('..', import.meta.url));
const artifacts = path.join(root, 'artifacts');
const base = 'http://127.0.0.1:8766';
const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
await page.emulateMedia({ reducedMotion: 'reduce' });
const errors = [], steps = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', msg => {
  if (msg.type() === 'error' && !(msg.location().url.includes('/api/validate') && msg.text().includes('400'))) errors.push(msg.text());
});
page.on('dialog', dialog => dialog.accept());
const configFile = config => ({ name: 'comparison.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(config)) });
async function navigate(view) { await page.locator(`.sidebar [data-view="${view}"]`).click(); }
async function openEvidence(id) {
  await navigate('evidence');
  await page.locator('#scope').selectOption(id);
  await page.locator('#evidence-content').getByRole('button', { name: '查看证据' }).first().click();
  await page.locator('#detail-content[aria-busy]').waitFor({ state: 'detached' });
}
async function tab(kind) {
  await page.locator(`#tab-${kind}`).click();
  await page.locator('#detail-content[aria-busy]').waitFor({ state: 'detached' });
}
async function downloaded(control, filename) {
  const [download] = await Promise.all([page.waitForEvent('download'), control.click()]);
  assert.equal(await download.failure(), null);
  const target = path.join(artifacts, filename);
  await download.saveAs(target);
  return fs.readFile(target, 'utf8');
}
try {
  await page.goto(base);
  await page.locator('#connection').getByText('已连接', { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelectorAll('#job-list tbody tr').length >= 2);
  const context = await (await page.request.get(base + '/api/context')).json();
  const read = context.jobs.find(j => j.name.includes('读取 MC'));
  const hold = context.jobs.find(j => j.name.includes('低权重'));
  assert.ok(read && hold);
  const readJob = await (await page.request.get(base + '/api/job?id=' + read.id)).json();
  await page.screenshot({ path: path.join(artifacts, 'overview-desktop.png'), fullPage: true });
  steps.push('独立服务、真实历史与初始总览');

  await openEvidence(read.id);
  await tab('samples');
  const rawSamples = await page.locator('#detail-content .sample-table').innerText();
  assert.match(rawSamples, /7\.131475[eE]-10/);
  assert.equal(await page.locator('#detail-content .sample-table tbody tr').count(), 4);
  assert.equal(await page.getByRole('button', { name: '下一页', exact: true }).isDisabled(), true);
  const csv = await downloaded(page.getByRole('link', { name: '下载样本原文件' }), 'read-samples.csv');
  assert.match(csv, /read_delay_s/);
  await page.screenshot({ path: path.join(artifacts, 'evidence-samples.png'), fullPage: true });
  await tab('config');
  assert.match(await page.locator('#detail-content').innerText(), /sram_6t_3x2_read/);
  await tab('report');
  assert.match(await page.locator('#detail-content').innerText(), /MC|monte|Monte/);
  await tab('log');
  assert.match(await page.locator('#detail-content').innerText(), /process\.log/);
  await page.keyboard.press('Escape');
  steps.push('逐轮样本、配置、报告、日志与原CSV下载');

  await openEvidence(hold.id);
  assert.match(await page.locator('#detail-content').innerText(), /low_observed_weight_mass/);
  assert.match(await page.locator('#detail-content').innerText(), /7\.8125%/);
  await page.locator('#close-detail').click();
  await navigate('matrix');
  await page.locator('#scope').selectOption('all');
  const analyzedJobs = await Promise.all(context.jobs.filter(j => j.analysis_available).map(async j => (await page.request.get(base + '/api/job?id=' + j.id)).json()));
  assert.equal(await page.locator('.matrix-cell').count(), analyzedJobs.reduce((sum, j) => sum + j.analysis.groups.reduce((n, g) => n + g.methods.length, 0), 0));
  await page.getByRole('button', { name: `${hold.name}，experiment，mnis，查看种子列表`, exact: true }).click();
  assert.equal(await page.locator('#method-content tbody tr').count(), 1);
  assert.doesNotMatch(await page.locator('#method-content').innerText(), /分组核对异常/);
  await page.getByRole('button', { name: '查看证据' }).click();
  await page.locator('#detail-title').getByText(/种子/).waitFor();
  await page.locator('#back-method').click();
  await page.locator('#close-detail').click();
  await page.screenshot({ path: path.join(artifacts, 'matrix-desktop.png'), fullPage: true });
  steps.push('独立参考、低权重提示、矩阵到方法再到种子的原索引下钻');

  await page.locator('#search').fill('不存在的策略');
  await page.getByText('没有匹配的方法或轮次', { exact: true }).waitFor();
  await page.locator('#search').fill('');
  await page.locator('#source-filter').selectOption('published');
  await page.getByText('暂无可对照的分析', { exact: true }).waitFor();
  await page.locator('#source-filter').selectOption('all');
  steps.push('搜索和来源筛选的空状态');

  await navigate('configure');
  await page.locator('#import').setInputFiles(configFile(context.presets[0].config));
  await page.waitForFunction(() => document.getElementById('json').value.includes('mnis-base'));
  await page.locator('#budget').fill('16');
  await page.getByRole('button', { name: '将表单写入 JSON', exact: true }).click();
  let config = JSON.parse(await page.locator('#json').inputValue());
  assert.deepEqual(config.estimators, context.presets[0].config.estimators);
  assert.equal(config.budget_per_algorithm, 16);
  await page.locator('#validate').click();
  await page.locator('#validation-message').getByText(/校验通过/).waitFor();
  assert.equal(await page.locator('#run').isDisabled(), false);
  await page.locator('#json').fill('{');
  assert.equal(await page.locator('#run').isDisabled(), true);
  await page.locator('#validate').click();
  assert.equal(await page.locator('#run').isDisabled(), true);
  await page.locator('#import').setInputFiles(configFile(readJob.config));
  await page.waitForFunction(() => document.getElementById('operation').value === 'read');
  assert.equal(await page.locator('#operation').inputValue(), 'read');
  await page.locator('#budget').fill('5');
  await page.getByRole('button', { name: '将表单写入 JSON', exact: true }).click();
  config = JSON.parse(await page.locator('#json').inputValue());
  assert.equal(config.experiment.case, 'sram_6t_3x2_read');
  assert.deepEqual(config.experiment.circuit.target, [1, 1]);
  steps.push('JSON导入、同算法多策略顺序、预算修改和固定电路标识保留');

  await page.locator('#import').setInputFiles(configFile(context.presets[0].config));
  await page.waitForFunction(() => document.getElementById('json').value.includes('mnis-base'));
  await page.locator('#validate').click();
  await page.locator('#validation-message').getByText(/6 条任务 · 72 条预算/).waitFor();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(artifacts, 'builder-guide.png'), fullPage: true });
  let release;
  const pause = new Promise(resolve => { release = resolve; });
  let intercepted;
  const requested = new Promise(resolve => { intercepted = resolve; });
  await page.route('**/api/validate', async route => { intercepted(); await pause; await route.continue(); });
  await page.locator('#validate').click();
  await requested;
  const changed = structuredClone(context.presets[0].config);
  changed.seeds = [9104];
  await page.locator('#json').fill(JSON.stringify(changed));
  release();
  await page.locator('#validation-message').getByText(/已忽略旧响应/).waitFor();
  assert.equal(await page.locator('#run').isDisabled(), true);
  await page.unroute('**/api/validate');
  steps.push('异步校验过期响应不会覆盖新输入或误启用运行');

  if (process.env.RUN_XYCE === '1' || process.env.LIVE_JOB_ID) {
    let created;
    if (process.env.LIVE_JOB_ID) created = { id: process.env.LIVE_JOB_ID };
    else {
      await page.locator('#import').setInputFiles(configFile(context.presets[0].config));
      await page.waitForFunction(() => document.getElementById('json').value.includes('mnis-base'));
      await page.locator('#experiment-name').fill('融合验收 · 两种MNIS × 三种子');
      await page.locator('#validate').click();
      await page.locator('#validation-message').getByText(/6 条任务 · 72 条预算/).waitFor();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(artifacts, 'validated-plan.png'), fullPage: true });
      const [response] = await Promise.all([
        page.waitForResponse(r => r.url() === base + '/api/run' && r.request().method() === 'POST'),
        page.locator('#run').click(),
      ]);
      assert.equal(response.status(), 200);
      created = await response.json();
      await fs.writeFile(path.join(artifacts, 'live-submission.json'), JSON.stringify(created, null, 2));
    }
    let job;
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      const response = await page.request.get(base + '/api/job?id=' + created.id);
      assert.ok(response.ok());
      job = await response.json();
      assert.equal(job.id, created.id);
      if (!['running', 'cancelling'].includes(job.status)) break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    assert.equal(job.status, 'complete', JSON.stringify({ status: job.status, error: job.error, log: job.log }));
    assert.equal(job.analysis.groups[0].runs.length, 6);
    assert.deepEqual(job.analysis.groups[0].methods.map(m => m.total_runs), [3, 3]);
    const returned = job.analysis.groups[0].runs.reduce((sum, r) => sum + (r.cost.xyce_returned || 0), 0);
    assert.ok(returned > 0 && returned <= 72);
    await fs.writeFile(path.join(artifacts, 'live-check.json'), JSON.stringify({ job, returned, planned_cap: 72 }, null, 2));
    await page.locator('#refresh').click();
    await navigate('matrix');
    await page.locator('#scope').selectOption(created.id);
    await page.waitForFunction(() => document.querySelectorAll('.matrix-cell').length === 2);
    await page.locator('.matrix-cell').first().click();
    assert.equal(await page.locator('#method-content tbody tr').count(), 3);
    await page.locator('#method-content').getByRole('button', { name: '查看证据' }).nth(2).click();
    assert.match(await page.locator('#detail-title').innerText(), /9103/);
    await page.locator('#close-detail').click();
    for (const name of ['runs.csv', 'methods.csv', 'groups.csv', 'references.csv']) {
      const contents = await downloaded(page.getByRole('link', { name, exact: true }), 'live-' + name);
      assert.ok(contents.length > 20);
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(artifacts, 'comparison-six-rounds.png'), fullPage: true });
    steps.push(`核验网页提交的六轮实验、两组各三轮汇总、四表下载；实际返回 ${returned} 条仿真`);
  }

  await page.reload();
  await page.locator('#connection').getByText('已连接', { exact: true }).waitFor();
  await navigate('evidence');
  await page.locator('#scope').selectOption(read.id);
  await page.waitForFunction(() => document.querySelectorAll('#evidence-content tbody tr').length === 1);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  await page.screenshot({ path: path.join(artifacts, 'evidence-mobile.png'), fullPage: true });
  await page.locator('#evidence-content').getByRole('button', { name: '查看证据' }).click();
  await tab('samples');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  await page.screenshot({ path: path.join(artifacts, 'samples-mobile.png'), fullPage: false });
  await page.locator('#close-detail').click();
  steps.push('刷新找回历史、小屏导航与证据详情无页面级横向溢出');
  const suiteSummary = context.jobs.find((j) => j.kind === "suite" && j.analysis_available);
  if (suiteSummary) {
    await navigate("matrix"); await page.locator("#scope").selectOption(suiteSummary.id);
    await page.waitForFunction(() => document.querySelectorAll(".matrix-table thead th").length >= 3);
    const headerText = await page.locator(".matrix-table thead").innerText();
    assert.match(headerText, /保持裕量/); assert.match(headerText, /读延迟/);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(artifacts, "suite-matrix.png"), fullPage: true });
    await navigate("evidence"); await page.locator("#scope").selectOption(suiteSummary.id);
    await page.locator("#evidence-content").getByRole("button", { name: "查看证据" }).first().click();
    await page.locator("#detail-content[aria-busy]").waitFor({ state: "detached" });
    await tab("circuit");
    await page.waitForFunction(() => { const img = document.querySelector(".waveform-img"); return img && img.naturalWidth > 0; });
    await page.screenshot({ path: path.join(artifacts, "circuit-evidence.png"), fullPage: false });
    const netlist = await downloaded(page.getByRole("link", { name: "网表 .sp" }).first(), "suite-netlist.sp");
    assert.ok(netlist.length > 100);
    await tab("samples");
    const dataset = await downloaded(page.getByRole("link", { name: "导出建模数据集（参数 → 测量）" }), "suite-dataset.csv");
    assert.match(dataset.split("\n")[0], /param_000/);
    assert.ok(dataset.trim().split("\n").length > 1);
    await page.locator("#close-detail").click();
    steps.push("套件矩阵按电路分列、电路证据波形与网表、建模数据集导出");
  }
  const manual = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  manual.on('pageerror', error => errors.push(error.message));
  await manual.goto(base + '/manual');
  assert.equal(await manual.locator('main section').count(), 12);
  await manual.evaluate(() => document.querySelectorAll('img').forEach(img => { img.loading = 'eager'; }));
  await manual.waitForFunction(() => [...document.images].length === 6 && [...document.images].every(img => img.complete && img.naturalWidth > 0));
  assert.equal(await manual.evaluate(() => [...document.querySelectorAll('nav a')].every(a => document.querySelector(a.hash))), true);
  await manual.screenshot({ path: path.join(artifacts, 'manual-desktop.png'), fullPage: false });
  await manual.evaluate(() => document.querySelectorAll('details').forEach(el => { el.open = true; }));
  const pdf = await manual.pdf({ path: path.join(root, '操作手册.pdf'), format: 'A4', printBackground: true, outline: true, tagged: true,
    displayHeaderFooter: true, headerTemplate: '<div></div>', footerTemplate: '<div style="width:100%;text-align:center;font-size:9px;color:#65788c">OpenYield Fusion · <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
    margin: { top: '16mm', bottom: '16mm', left: '15mm', right: '15mm' } });
  assert.equal(pdf.subarray(0, 5).toString(), '%PDF-');
  const logResponse = await manual.request.get(base + '/MILESTONES.md');
  assert.equal(logResponse.status(), 200);
  await manual.setViewportSize({ width: 390, height: 844 });
  assert.ok(await manual.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await manual.goto(new URL('../操作手册.html', import.meta.url).href);
  await manual.evaluate(() => document.querySelectorAll('img').forEach(img => { img.loading = 'eager'; }));
  await manual.waitForFunction(() => [...document.images].length === 6 && [...document.images].every(img => img.complete && img.naturalWidth > 0));
  await manual.close();
  steps.push('新手手册12章、六张实图、目录锚点、日志入口和离线阅读');
  const staticPage = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  staticPage.on('pageerror', error => errors.push(error.message));
  await staticPage.goto(new URL('../demo_site/index.html', import.meta.url).href);
  await staticPage.locator('#connection').getByText('已连接', { exact: true }).waitFor();
  assert.equal(await staticPage.locator('#demo-banner').isVisible(), true);
  await staticPage.locator('.sidebar [data-view="matrix"]').click();
  await staticPage.waitForFunction(() => document.querySelectorAll('.matrix-table thead th').length >= 3);
  await staticPage.locator('.sidebar [data-view="evidence"]').click();
  await staticPage.locator('#evidence-content').getByRole('button', { name: '查看证据' }).first().click();
  await staticPage.locator('#detail-content[aria-busy]').waitFor({ state: 'detached' });
  await staticPage.locator('#tab-circuit').click();
  await staticPage.waitForFunction(() => { const img = document.querySelector('.waveform-img'); return img && img.naturalWidth > 0; });
  await staticPage.locator('#tab-samples').click();
  await staticPage.locator('#detail-content[aria-busy]').waitFor({ state: 'detached' });
  const [staticDownload] = await Promise.all([
    staticPage.waitForEvent('download'),
    staticPage.getByRole('link', { name: '导出建模数据集（参数 → 测量）' }).click(),
  ]);
  assert.equal(await staticDownload.failure(), null);
  await staticPage.locator('#close-detail').click();
  await staticPage.locator('.sidebar [data-view="configure"]').click();
  assert.equal(await staticPage.locator('#validate').isDisabled(), true);
  assert.equal(await staticPage.locator('#suite-run').isDisabled(), true);
  await staticPage.screenshot({ path: path.join(artifacts, 'demo-static.png'), fullPage: false });
  await staticPage.close();
  steps.push('静态演示站点：只读横幅、套件矩阵、波形数据URL、数据集下载、运行按钮禁用');
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(artifacts, 'browser-checks.json'), JSON.stringify({ checked_at: new Date().toISOString(), passed: true, steps, errors }, null, 2));
  console.log(JSON.stringify({ passed: true, steps }, null, 2));
} catch (error) {
  await page.screenshot({ path: path.join(artifacts, 'browser-failure.png'), fullPage: true }).catch(() => {});
  await fs.writeFile(path.join(artifacts, 'browser-checks.json'), JSON.stringify({ checked_at: new Date().toISOString(), passed: false, steps, errors, error: error.stack }, null, 2));
  throw error;
} finally {
  await browser.close();
}
