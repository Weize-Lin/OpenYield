"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const names = ["mc", "mnis", "ais", "acs", "hscs"];
  const kinds = ["summary", "samples", "config", "report", "log", "circuit"];
  const kindNames = { summary: "概览", samples: "样本", config: "配置", report: "原报告", log: "日志", circuit: "电路证据" };
  const DEMO = window.DEMO_DATA || null;
  function demoBlob(entry) {
    if (entry.base64) {
      const bytes = Uint8Array.from(atob(entry.base64), (c) => c.charCodeAt(0));
      return new Blob([bytes], { type: entry.mime || "application/octet-stream" });
    }
    return new Blob([entry.text || ""], { type: entry.mime || "text/plain; charset=utf-8" });
  }
  function demoResponse(path, data) {
    const url = new URL(path, "http://demo.local");
    const q = url.searchParams;
    const key = (...parts) => parts.join("|");
    const ok = (value) => ({ ok: true, status: 200, json: async () => value, text: async () => JSON.stringify(value), blob: async () => new Blob([JSON.stringify(value)], { type: "application/json" }) });
    const fail = (message) => ({ ok: false, status: 400, json: async () => ({ error: message }), text: async () => message });
    if (data !== undefined) return fail("演示模式只读：不能校验新配置或启动仿真。");
    const p = url.pathname;
    if (p === "/api/context") return ok(DEMO.context);
    if (p === "/api/jobs") return ok({ jobs: DEMO.jobs });
    if (p === "/api/job") return DEMO.details[q.get("id")] ? ok(DEMO.details[q.get("id")]) : fail("演示包未包含该实验。");
    if (p === "/api/evidence") {
      const entry = DEMO.evidence[key(q.get("id"), q.get("group"), q.get("run"), q.get("kind"), q.get("page") || "1")];
      return entry ? ok(entry) : fail("演示包未包含该页证据。");
    }
    if (p === "/api/download") return { ok: true, status: 200, blob: async () => demoBlob({ text: DEMO.tables[key(q.get("id"), q.get("name"))], mime: "text/csv" }) };
    if (p === "/api/dataset") return { ok: true, status: 200, blob: async () => demoBlob({ text: DEMO.dataset[key(q.get("id"), q.get("group"), q.get("run"))], mime: "text/csv" }) };
    if (p === "/api/asset") return { ok: true, status: 200, blob: async () => demoBlob(DEMO.assets[key(q.get("id"), q.get("group"), q.get("run"), q.get("kind"))]) };
    if (p === "/api/batch-file") return { ok: true, status: 200, blob: async () => demoBlob(DEMO.batch[key(q.get("id"), q.get("group"), q.get("run"), q.get("batch"), q.get("name"))]) };
    return fail("演示模式未提供该接口。");
  }
  // These are exactly the cohort fields in upstream summarize_methods().
  const budgetFields = ["budget_protocol", "budget", "draw_cap", "xyce_cap", "estimation_cap", "time_limit_s", "legacy_budget_basis"];
  const statusNames = {
    running: "运行中", analyzing: "分析中", pending: "待处理", cancelling: "取消中",
    complete: "已完成", completed: "已完成", incomplete: "未完整完成", timeout: "已超时",
    error: "报错", failed: "失败", cancelled: "已取消", canceled: "已取消", interrupted: "服务中断，待核对",
    budget_stopped: "预算停止", stopped: "已停止", missing_summary: "缺少概要",
    invalid_summary: "概要无效", linear: "线性估计可用", log_only: "仅对数估计",
    no_numeric_estimate: "无有效数值估计", integrity_or_seed: "完整性或种子不符",
    differs_from_plan: "偏离计划", invalid_cost_evidence: "成本证据无效",
    available: "可用", not_specified: "未指定", not_available: "不可用",
    same: "一致", different: "不同", unknown: "未知", planned_target: "计划目标",
  };
  const warningNames = {
    low_observed_weight_mass: "观测权重质量偏低", estimate_above_one: "失效概率估计超过 1",
    missing_summary: "缺少概要文件", invalid_summary: "概要文件无效",
    summary_sha256_mismatch: "概要文件哈希不符", missing_revision_or_dirty_flag: "缺少源码版本或修改标记",
    modified_source_not_reconstructable_from_commit: "源码有未提交改动，无法仅凭提交重建",
    missing_model_hash: "缺少模型哈希", missing_solver_path_or_version: "缺少求解器路径或版本",
    missing_or_invalid_solver_sha256: "求解器哈希缺失或无效", reference_hash_not_pinned: "参考文件哈希未固定",
    target_differs_from_plan: "目标工况偏离计划", estimator_profile_differs_from_plan: "策略配置偏离计划",
    budget_differs_from_plan: "预算偏离计划", missing_or_invalid_cost_counts: "成本计数缺失或无效",
    inconsistent_cost_counts: "请求、提交与返回计数不一致", unclassified_or_unreturned_samples: "存在未分类或未返回样本",
    underflow: "数值下溢", overflow: "数值上溢",
  };
  const state = {
    context: null, jobs: [], details: new Map(), detailErrors: new Map(), pendingDetails: new Map(),
    view: "overview", scope: "all", source: "all", query: "", ready: false, initializedForm: false, historyLoaded: false,
    configRevision: 0, validated: null, formDirty: false, jsonStaleForm: false,
    touched: new Set(), validating: false, runPending: false, analyzePending: false,
    suiteRevision: 0, suiteValidated: null, suiteValidating: false, suiteRunPending: false,
    refreshPending: false, pollTimer: null, loading: false, cancelPending: new Set(),
    modal: null, modalSequence: 0, modalController: null, modalReturn: null,
  };
  const text = (value) => value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
  const pretty = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "未提供内容";
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const fmt = (value) => finite(value) ? new Intl.NumberFormat("zh-CN", { maximumSignificantDigits: 6 }).format(value) : "—";
  const percent = (value) => finite(value) ? `${fmt(value * 100)}%` : "—";
  const points = (value) => finite(value) ? `${fmt(value * 100)} 个百分点` : "—";
  const labelStatus = (value) => statusNames[value] || text(value);
  const active = (job) => ["running", "analyzing", "pending", "cancelling"].includes(job.status);
  const busy = () => state.jobs.some(active) || state.runPending || state.analyzePending;
  const sourceName = (value) => value === "published" ? "公开证据" : value === "local" ? "本地实验" : value === "imported" ? "导入证据（来源待核对）" : "未记录来源";
  const errorText = (error) => error?.message || String(error);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const queryURL = (path, values) => `${path}?${new URLSearchParams(values)}`;
  const node = (tag, cls, value) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (value !== undefined) el.textContent = text(value);
    return el;
  };
  const fragment = (...children) => {
    const part = document.createDocumentFragment();
    part.append(...children);
    return part;
  };
  function button(title, handler, cls = "secondary") {
    const el = node("button", cls, title);
    el.type = "button";
    el.addEventListener("click", () => Promise.resolve().then(() => handler(el)).catch(showError));
    return el;
  }
  function listen(id, event, fn) {
    $(id).addEventListener(event, (e) => {
      // Call synchronously so submit prevention and duplicate-click locks happen immediately.
      try { Promise.resolve(fn(e)).catch(showError); } catch (error) { showError(error); }
    });
  }
  function showError(error) {
    $("global-message-text").textContent = errorText(error);
    $("global-message").hidden = false;
    $("global-message").className = "notice error";
    // A modal makes the page behind it inert; report failures inside it as well.
    if ($("detail-dialog").open) {
      const host = $("method-content").hidden ? $("detail-content") : $("method-content");
      const notice = node("div", "notice error", errorText(error));
      notice.setAttribute("role", "alert");
      notice.append(button("关闭提示", () => notice.remove(), "quiet"));
      host.prepend(notice);
    }
  }
  function feedback(message, good = false) {
    $("validation-message").textContent = message;
    $("validation-message").className = `feedback ${good ? "success" : "error"}`;
  }
  async function request(path, data, signal) {
    if (DEMO) return demoResponse(path, data);
    const controller = new AbortController();
    const forwardAbort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener("abort", forwardAbort, { once: true });
    const timer = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch(path, {
        method: data === undefined ? "GET" : "POST", signal: controller.signal,
        headers: data === undefined ? {} : { "Content-Type": "application/json", "X-OpenYield-Token": state.context?.token || "" },
        body: data === undefined ? undefined : JSON.stringify(data), cache: "no-store",
      });
      if (!response.ok) {
        const body = await response.text();
        let reason = body;
        try { reason = JSON.parse(body).error || body; } catch { /* Plain-text HTTP errors are also displayed. */ }
        throw new Error(`HTTP ${response.status} · ${String(reason).slice(0, 600) || response.statusText}`);
      }
      return response;
    } catch (error) {
      if (error.name === "AbortError" && !signal?.aborted) throw new Error("请求超时，请重试；不会自动重复提交任务。");
      throw error;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", forwardAbort);
    }
  }
  async function api(path, data, signal) {
    const response = await request(path, data, signal);
    try { return await response.json(); } catch { throw new Error("服务端返回了无法解析的 JSON。"); }
  }
  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = node("a");
    a.href = url; a.download = filename;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function downloadLink(title, url, filename) {
    const a = node("a", "download", title);
    a.href = url; a.download = filename;
    a.addEventListener("click", (event) => {
      event.preventDefault();
      if (a.getAttribute("aria-busy") === "true") return;
      a.setAttribute("aria-busy", "true");
      request(url).then((response) => response.blob()).then((blob) => saveBlob(blob, filename))
        .catch((error) => {
          showError(error);
          const message = node("p", "notice error", `下载失败 · ${errorText(error)}`);
          a.parentElement?.append(message);
        }).finally(() => a.removeAttribute("aria-busy"));
    });
    return a;
  }
  function table(headers, rows, cls = "") {
    const wrap = node("div", `table-wrap ${cls}`);
    const t = node("table");
    const head = t.createTHead().insertRow();
    headers.forEach((h) => { const th = node("th", "", h); th.scope = "col"; head.append(th); });
    const body = t.createTBody();
    rows.forEach((row) => {
      const tr = body.insertRow();
      row.forEach((value) => { const td = tr.insertCell(); value instanceof Node ? td.append(value) : td.textContent = text(value); });
    });
    wrap.append(t);
    return wrap;
  }
  function badge(status) {
    const cls = active({ status }) ? "accent" : ["complete", "completed", "available", "linear", "same"].includes(status) ? "good"
      : ["error", "failed", "invalid_summary", "different"].includes(status) ? "bad" : "warning";
    const el = node("span", `badge ${cls}`, labelStatus(status));
    el.title = text(status);
    return el;
  }
  function empty(title, message, importAction = true) {
    const el = node("div", "empty");
    el.append(node("h3", "", title), node("p", "", message));
    if (importAction) el.append(button("载入已有证据", goImport));
    return el;
  }
  function dateLabel(value) {
    if (value == null) return "时间未记录";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleString("zh-CN", { hour12: false });
  }
  function jobName(job) { return job.name || job.id; }
  function groupName(group, index) { return group.name || group.id || `工况组 ${index + 1}`; }
  function groupsOf(job) { return Array.isArray(job?.analysis?.groups) ? job.analysis.groups : []; }
  function filteredJobs() {
    return state.jobs.filter((j) => (state.scope === "all" || state.scope === j.id) && (state.source === "all" || state.source === j.source));
  }
  function matchesRun(run) {
    if (!state.query) return true;
    return [run.estimator, run.seed, run.status, labelStatus(run.status), run.estimate_status, labelStatus(run.estimate_status), run.implementation, pretty(run.strategy || {})]
      .some((value) => String(value ?? "").toLowerCase().includes(state.query));
  }
  function visibleGroups() {
    return filteredJobs().flatMap((summary) => {
      const job = state.details.get(summary.id);
      return groupsOf(job).map((group, g) => ({ job, group, g }));
    });
  }
  function navigate(view) {
    if (!["overview", "matrix", "evidence", "configure"].includes(view)) view = "overview";
    state.view = view;
    const titles = { overview: "实验总览", matrix: "对照矩阵", evidence: "证据记录", configure: "配置实验" };
    $("page-title").textContent = titles[view];
    for (const key of Object.keys(titles)) $("view-" + key).hidden = view !== key;
    document.querySelectorAll(".sidebar [data-view]").forEach((el) => {
      if (el.dataset.view === view) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
    });
    $("data-toolbar").hidden = view === "configure";
    history.replaceState(null, "", `#${view}`);
    renderView();
  }
  function goImport() {
    navigate("overview");
    $("import-panel").scrollIntoView({ block: "start" });
    $("manifest").focus({ preventScroll: true });
  }
  function syncScope() {
    const options = JSON.stringify(state.jobs.map((j) => [j.id, jobName(j)]));
    if ($("scope").dataset.options === options) return;
    $("scope").dataset.options = options;
    $("scope").replaceChildren(new Option("全部已有分析", "all"), ...state.jobs.map((j) => new Option(jobName(j), j.id)));
    if (state.scope !== "all" && !state.jobs.some((j) => j.id === state.scope)) state.scope = "all";
    $("scope").value = state.scope;
  }
  function syncButtons() {
    const validated = state.validated;
    const canRun = validated && validated.revision === state.configRevision && validated.text === $("json").value
      && validated.contextRevision === state.context?.revision && !state.formDirty;
    const demo = Boolean(state.context?.demo);
    const suiteCanRun = state.suiteValidated && state.suiteValidated.text === $("suite-json").value;
    $("run").disabled = !state.ready || !canRun || busy() || demo;
    $("validate").disabled = !state.ready || state.validating || demo;
    $("analyze").disabled = !state.ready || busy() || demo;
    $("suite-validate").disabled = !state.ready || state.suiteValidating || demo;
    $("suite-run").disabled = !state.ready || !suiteCanRun || busy() || demo || state.suiteRunPending;
    $("demo-banner").hidden = !demo;
    document.querySelectorAll("[data-catalog-import]").forEach((el) => { el.disabled = !state.ready || busy() || demo; });
    $("run-state").textContent = state.runPending ? "正在提交运行请求…" : state.jobs.some(active) ? "当前有活动任务；结束后可启动下一场，不设队列。" : "无活动任务 · 仅点击运行按钮才启动仿真";
  }
  async function loadJob(id, force = false) {
    if (state.pendingDetails.has(id)) return state.pendingDetails.get(id);
    if (!force && state.details.has(id)) return state.details.get(id);
    const promise = api(queryURL("/api/job", { id })).then((job) => {
      state.details.set(id, job); state.detailErrors.delete(id); return job;
    }).catch((error) => { state.detailErrors.set(id, errorText(error)); throw error; })
      .finally(() => state.pendingDetails.delete(id));
    state.pendingDetails.set(id, promise);
    return promise;
  }
  function schedulePoll() {
    clearTimeout(state.pollTimer);
    if (state.jobs.some(active) && !document.hidden) {
      state.pollTimer = setTimeout(() => refreshJobs().catch(showError), 2500);
    }
  }
  async function refreshJobs(force = false) {
    if (state.refreshPending) return;
    state.refreshPending = true; $("refresh").disabled = true;
    try {
      const before = JSON.stringify(state.jobs);
      const previous = new Map(state.jobs.map((job) => [job.id, job]));
      const result = await api("/api/jobs");
      if (!Array.isArray(result.jobs)) throw new Error("实验历史响应缺少 jobs 列表。");
      state.jobs = result.jobs;
      syncScope(); syncButtons();
      let changed = before !== JSON.stringify(state.jobs) || force || !state.historyLoaded;
      state.historyLoaded = true;
      const toLoad = state.jobs.filter((j) => {
        const cached = state.details.get(j.id);
        const transitioned = previous.has(j.id) && previous.get(j.id).status !== j.status;
        if (!force && !active(j) && !transitioned && state.detailErrors.has(j.id)) return false;
        return (j.analysis_available || active(j) || state.scope === j.id) &&
          (force || !cached || active(j) || active(previous.get(j.id) || {}) || cached.status !== j.status);
      });
      // Bound parallel reads; never issue an analysis or simulation from history loading.
      let cursor = 0;
      await Promise.all(Array.from({ length: Math.min(3, toLoad.length) }, async () => {
        while (cursor < toLoad.length) {
          const summary = toLoad[cursor++];
          const old = state.details.get(summary.id);
          try {
            const job = await loadJob(summary.id, true);
            if (!old || JSON.stringify([old.status, old.analysis, old.error]) !== JSON.stringify([job.status, job.analysis, job.error])) changed = true;
          } catch (error) { changed = true; showError(`读取 ${jobName(summary)} 失败 · ${errorText(error)}`); }
        }
      }));
      $("connection").textContent = "已连接"; $("connection").className = "badge good";
      if (changed) renderView();
    } catch (error) {
      $("connection").textContent = "连接异常"; $("connection").className = "badge bad";
      throw error;
    } finally {
      state.refreshPending = false; $("refresh").disabled = false; syncButtons(); schedulePoll();
    }
  }
  function renderView() {
    if (state.view === "overview") renderOverview();
    if (state.view === "matrix") renderMatrix();
    if (state.view === "evidence") renderEvidence();
    // Configuration and open evidence dialogs are never redrawn by polling.
  }
  function renderOverview() {
    const jobs = filteredJobs();
    const stats = [["实验记录", jobs.length, "当前实验与来源范围"], ["已有分析", jobs.filter((j) => j.analysis_available).length, "保留各实验独立统计"], ["活动任务", jobs.filter(active).length, "手动启动 · 单任务"], ["可用独立参考组", visibleGroups().filter(({ group }) => group.reference?.status === "available").length, "仅计已载入分析的组"]];
    $("overview-stats").replaceChildren(...stats.map(([title, value, note]) => {
      const el = node("div", "stat-card");
      el.append(node("span", "stat-label", title), node("strong", "", state.historyLoaded ? value : "—"), node("small", "", note));
      return el;
    }));
    const selected = jobs.filter((job) => {
      if (!state.query) return true;
      return [jobName(job), job.status, labelStatus(job.status)].some((v) => String(v).toLowerCase().includes(state.query)) ||
        groupsOf(state.details.get(job.id)).some((g) => (g.runs || []).some(matchesRun));
    });
    if (!selected.length) {
      $("job-list").replaceChildren(empty(state.ready ? "暂无匹配实验" : "等待连接研究环境", "可载入已有结果查看证据，无需启动仿真。"));
      return;
    }
    $("job-list").replaceChildren(table(["实验 / 来源", "状态 / 进度", "创建时间", "结果与操作"], selected.map((job) => {
      const title = node("div"); title.append(node("span", "job-name", jobName(job)), node("span", "badge", sourceName(job.source)), node("p", "path", job.id));
      const status = node("div"); status.append(badge(job.status), node("p", "hint", `已结束 ${text(job.finished_tasks)} / 计划 ${text(job.plan_count)}`));
      if (job.error) status.append(node("p", "feedback error", job.error));
      if (state.detailErrors.has(job.id)) status.append(node("p", "feedback error", state.detailErrors.get(job.id)));
      const actions = node("div", "toolbar");
      if (job.analysis_available) {
        actions.append(button("查看矩阵", () => selectJob(job.id, "matrix")), button("证据记录", () => selectJob(job.id, "evidence"), "quiet"));
      }
      actions.append(button("任务详情", () => openJob(job.id), "quiet"));
      if (active(job) && job.source !== "published") actions.append(cancelButton(job));
      return [title, status, dateLabel(job.created_at), actions];
    })));
  }
  async function selectJob(id, view) {
    state.scope = id; $("scope").value = id;
    await loadJob(id); navigate(view);
  }
  function csvLinks(job) {
    const toolbar = node("div", "toolbar");
    if (job.analysis) ["runs.csv", "methods.csv", "groups.csv", "references.csv"].forEach((name) => {
      toolbar.append(downloadLink(name, queryURL("/api/download", { id: job.id, name }), name));
    });
    return toolbar;
  }
  function cancelButton(job) {
    const el = button("取消任务", async (control) => {
      if (state.cancelPending.has(job.id)) return;
      control.disabled = true; state.cancelPending.add(job.id);
      try {
        const result = await api("/api/cancel", { id: job.id });
        await acceptJob(result, false);
        control.textContent = "已发送取消";
      } finally { state.cancelPending.delete(job.id); control.disabled = false; }
    }, "danger");
    el.disabled = state.cancelPending.has(job.id);
    return el;
  }
  function condition(group) {
    const run = (group.runs || []).find((r) => r.target_status === "same") || (group.runs || []).find((r) => r.target?.resolved_circuit);
    const target = run?.target, c = target?.resolved_circuit;
    if (!c) return "工况定义未完整记录，请核对原报告。";
    const operation = { hold_snm: "保持裕量", read_snm: "读裕量", write_snm: "写裕量", read: "读延迟", write: "写延迟", "read&write": "读写周期" }[target.operation] || text(target.operation);
    return `${text(c.cell_type).replace("SRAM_", "").replace("_CELL", "")} · ${Array.isArray(c.array) ? c.array.slice(0, 2).join(" × ") : "—"} · ${operation} · ${text(c.corner)} · ${text(c.vdd)} V · ${text(c.temperature_c)} ℃ · 门限 ${text(target.threshold)} ${target.threshold_field === "threshold_mv" ? "mV" : "ns"}`;
  }
  function referenceBlock(group) {
    const ref = group.reference || {};
    const box = node("div", "reference-card");
    const top = node("div"); top.append(node("span", "cell-label", "独立参考  "), node("span", "reference-value", percent(ref.estimate)), badge(ref.status));
    box.append(top);
    if (ref.status === "available") {
      box.append(node("p", "", `失效 ${text(ref.failure_count)} / 样本 ${text(ref.samples)} · 95% 区间 ${Array.isArray(ref.interval) ? ref.interval.map(percent).join(" – ") : "未提供"}`));
      if (!ref.provenance_complete) box.append(node("p", "feedback error", "参考数值可用，但来源验证不完整；不能据此宣称严格同源比较。"));
    } else box.append(node("p", "", `参考未指定、缺失或未通过检查（${text(ref.status)}）。误差不按零计算。`));
    if (ref.issues?.length) box.append(node("p", "", `参考问题：${ref.issues.map(warningLabel).join("；")}`));
    const raw = node("details");
    raw.append(node("summary", "", "独立参考来源 / 检查原值"), node("pre", "", pretty(ref)));
    box.append(raw);
    return box;
  }
  function checksBlock(group) {
    const el = node("div", "group-checks");
    const c = group.checks || {};
    [["总轮次", c.total_runs], ["同目标", c.target_same], ["目标不同", c.target_different], ["来源不完整", c.source_incomplete], ["计划偏离", c.plan_mismatch]].forEach(([key, value]) => el.append(node("span", "", `${key} ${text(value)}`)));
    return el;
  }
  // Canonical object comparison matches Python signature(sort_keys=True), without hashing collisions.
  function canonical(value) {
    if (value == null) return "null";
    if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
    if (typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
    return JSON.stringify(value);
  }
  function cohortForRun(run) {
    const planned = run.planned;
    const budget = Object.fromEntries(budgetFields.map((key) => [key, (planned != null ? planned[key] : run.cost?.[key]) ?? null]));
    return [run.estimator, planned != null ? (run.target_status === "different" ? "different" : "planned_target") : run.target_status,
      budget, planned != null ? { estimator_profile: planned.profile } : run.strategy];
  }
  function cohortForMethod(method) { return [method.estimator, method.target_cohort, method.budget_tier, method.strategy]; }
  function methodRuns(group, method) {
    // Never fall back to estimator-only matching: one ID may contain multiple budgets/strategies.
    if (method.target_cohort == null || method.budget_tier == null || method.strategy == null) return null;
    const key = canonical(cohortForMethod(method));
    return (group.runs || []).map((run, r) => ({ run, r })).filter(({ run }) => canonical(cohortForRun(run)) === key);
  }
  function methodKey(method, columnIndex, methodIndex) {
    if (method.target_cohort == null || method.budget_tier == null || method.strategy == null) return `unmapped:${columnIndex}:${methodIndex}`;
    return canonical(cohortForMethod(method));
  }
  function methodDescription(method) {
    const budget = method.budget_tier || {};
    const profile = method.strategy?.estimator_profile || method.strategy || {};
    const parts = [profile.algorithm?.toUpperCase()];
    if (profile.initialization?.method) parts.push(profile.initialization.method === "uniform_box" ? `均匀找点 ±${text(profile.initialization.radius_sigma)}σ` : `找点 ${profile.initialization.method}`);
    if (finite(profile.proposal?.target_fraction)) parts.push(`目标分布混合 ${percent(profile.proposal.target_fraction)}`);
    const summary = parts.filter(Boolean).join(" · ");
    return `${summary || "策略见原始配置"} · 预算 ${text(budget.budget)} · 抽样上限 ${text(budget.draw_cap)} · ${labelStatus(method.target_cohort)}`;
  }
  function noScore(method, group) {
    if (method.mean_absolute_error != null) return "误差为相对本组独立参考的绝对误差";
    if (group.reference?.status !== "available") return "未评分：独立参考不可用或未通过检查";
    if (!method.linear_metrics_runs) return "未评分：无符合工况与已知预算层级的线性估计（可能仅有对数估计）";
    return "未评分：上游未提供误差；请核对目标、预算及估计有效性";
  }
  function readErrors(container) {
    filteredJobs().forEach((job) => {
      if (state.detailErrors.has(job.id)) {
        const message = node("div", "notice error", `${jobName(job)} · ${state.detailErrors.get(job.id)}`);
        message.append(button("重试读取", async () => { await loadJob(job.id, true); renderView(); }));
        container.append(message);
      }
    });
  }
  function renderMatrix() {
    const container = $("matrix-content"); container.replaceChildren(); readErrors(container);
    const columns = visibleGroups();
    if (!columns.length) { container.append(empty("暂无可对照的分析", "先载入已有证据，或调整实验与来源筛选。")); return; }
    const rows = new Map();
    columns.forEach(({ group }, col) => (group.methods || []).forEach((method, mi) => {
      const revisions = [...new Set((methodRuns(group, method) || []).map(({ run }) => run.source?.git_revision).filter(Boolean))].sort();
      const key = methodKey(method, col, mi) + canonical(revisions.length ? revisions : [`unverified:${col}`]);
      if (!rows.has(key)) rows.set(key, { method, revisions, cells: new Map() });
      rows.get(key).cells.set(col, method);
    }));
    const wrap = node("div", "table-wrap"); const t = node("table", "matrix-table");
    const header = t.createTHead().insertRow();
    const first = node("th", "", "方法 / 策略版本"); first.scope = "col"; header.append(first);
    columns.forEach(({ job, group, g }) => {
      const th = node("th"); th.scope = "col";
      th.append(node("div", "method-title", jobName(job)), node("p", "hint", groupName(group, g)), node("p", "hint", condition(group)), node("p", "hint", `独立参考 ${percent(group.reference?.estimate)} · ${labelStatus(group.reference?.status)}`));
      header.append(th);
    });
    const body = t.createTBody(); let visible = 0;
    for (const { method, revisions, cells } of rows.values()) {
      const match = !state.query || [...cells].some(([col, m]) => {
        const runs = methodRuns(columns[col].group, m);
        return String(m.estimator).toLowerCase().includes(state.query) || (runs || []).some(({ run }) => matchesRun(run));
      });
      if (!match) continue;
      visible++;
      const tr = body.insertRow(); const title = node("th"); title.scope = "row";
      title.append(node("div", "method-title", method.estimator), node("span", "badge", `源码 ${revisions.length ? revisions.map(v => v.slice(0, 7)).join(" / ") : "未核实"}`), node("div", "strategy-summary", methodDescription(method)));
      tr.append(title);
      columns.forEach(({ job, group, g }, col) => {
        const td = tr.insertCell(), m = cells.get(col);
        if (!m) { td.append(node("div", "matrix-missing", "—\n该实验组无此策略 / 预算层级")); return; }
        const cell = button("", () => openMethod(job, g, m), "matrix-cell");
        cell.setAttribute("aria-label", `${jobName(job)}，${groupName(group, g)}，${m.estimator}，查看种子列表`);
        cell.append(node("span", "badge accent", `有估计 ${text(m.estimates_available)} / ${text(m.total_runs)} 轮`), node("span", "big", percent(m.mean_estimate)), node("span", "cell-label", "平均失效概率估计"), node("span", "cell-error", `平均绝对误差 ${points(m.mean_absolute_error)}`), node("span", "cell-meta", `无估计 ${text(m.no_estimate_runs)} · 低权重告警 ${text(m.weight_warning_runs)} · 超 1 告警 ${text(m.above_one_runs)}`), node("span", "cell-meta", "成本：进入种子列表查看四项原值"));
        const indexed = methodRuns(group, m);
        if (indexed && indexed.length === m.total_runs) {
          const costRows = indexed.filter(({ run }) => finite(run.cost?.xyce_returned) && finite(run.cost?.xyce_submitted));
          if (costRows.length) cell.append(node("span", "cell-meta", `已记账 ${costRows.length}/${m.total_runs} 轮 · 返回 ${costRows.reduce((n, { run }) => n + run.cost.xyce_returned, 0)} / 提交 ${costRows.reduce((n, { run }) => n + run.cost.xyce_submitted, 0)}${costRows.length < m.total_runs ? "（不含未知）" : ""}`));
          const wallRows = indexed.filter(({ run }) => finite(run.cost?.wall_seconds));
          if (wallRows.length) cell.append(node("span", "cell-meta", `耗时合计 ${fmt(wallRows.reduce((n, { run }) => n + run.cost.wall_seconds, 0))} 秒${wallRows.length < m.total_runs ? "（不含未知）" : ""}`));
        }
        cell.append(node("span", "cell-meta", `种子间标准差 ${points(m.seed_std)}`));
        cell.append(node("span", "cell-meta", `均值纳入 ${text(m.linear_metrics_runs)} 轮 · 误差纳入 ${text(m.error_runs)} 轮`));
        if (m.mean_absolute_error == null) cell.append(node("span", "cell-meta", noScore(m, group)));
        td.append(cell);
      });
    }
    if (!visible) { container.append(empty("没有匹配的方法或轮次", "调整搜索条件。筛选不会移除统计分母中的缺失轮。", false)); return; }
    wrap.append(t); container.append(wrap);
    const exports = node("div", "card"); exports.style.marginTop = "20px";
    exports.append(node("h3", "", "上游原表导出"));
    filteredJobs().forEach((summary) => {
      const job = state.details.get(summary.id);
      if (!job?.analysis) return;
      exports.append(node("p", "hint", jobName(job)), csvLinks(job));
    });
    container.append(exports);
  }
  function warningLabel(code) { return `${warningNames[code] || "诊断告警"} [${text(code)}]`; }
  function runRows(job, g, indexed) {
    return indexed.map(({ run, r }) => {
      const method = fragment(node("span", "job-name", run.estimator), node("span", "path", run.implementation || "实现未记录"));
      const status = fragment(badge(run.status), node("p", "hint", labelStatus(run.estimate_status)));
      const estimate = fragment(node("strong", "", percent(run.pfail_estimate)), node("p", "hint", `log₁₀ ${text(run.log10_pfail)}`));
      const cost = run.cost || {};
      const costs = fragment(node("span", "", `${text(cost.algorithm_requests)} / ${text(cost.xyce_submitted)} / ${text(cost.xyce_returned)}`), node("p", "hint", `正式分母 ${text(cost.formal_draws)}`));
      const warn = (run.warnings || []).map(warningLabel).join("；") || "未记录告警";
      return [method, run.seed, status, estimate, points(run.absolute_error), costs, warn, button("查看证据", () => openRun(job, g, r), "quiet")];
    });
  }
  const runHeaders = ["方法", "种子", "状态", "原始估计", "绝对误差", "请求 / 提交 / 返回", "告警", "详情"];
  function renderEvidence() {
    const container = $("evidence-content"); container.replaceChildren(); readErrors(container);
    let count = 0;
    visibleGroups().forEach(({ job, group, g }) => {
      // Map before filtering: the API requires the original analysis array indices.
      const indexed = (group.runs || []).map((run, r) => ({ run, r })).filter(({ run }) => matchesRun(run));
      if (!indexed.length) return;
      count += indexed.length;
      const section = node("section", "card");
      const heading = node("div", "group-heading");
      const info = node("div"); info.append(node("h3", "", `${jobName(job)} / ${groupName(group, g)}`), node("p", "hint", condition(group)));
      heading.append(info, badge(job.status));
      section.append(heading, referenceBlock(group), checksBlock(group), node("p", "hint", `展示 ${indexed.length} / 本组 ${text(group.checks?.total_runs ?? group.runs?.length)} 轮 · “—”表示未提供或未评分，非零值`), table(runHeaders, runRows(job, g, indexed)), csvLinks(job));
      container.append(section);
    });
    if (!count) container.append(empty("暂无匹配的证据记录", "可载入已有结果，或调整方法、种子、状态与来源筛选。"));
  }

  function openDialog(title, subtitle, eyebrow = "RUN EVIDENCE") {
    $("detail-title").textContent = title; $("detail-subtitle").textContent = subtitle; $("detail-eyebrow").textContent = eyebrow;
    if (!$("detail-dialog").open) {
      state.modalReturn = document.activeElement;
      $("detail-dialog").showModal();
      $("close-detail").focus();
    }
  }
  function stopEvidenceRequest() {
    state.modalSequence++; state.modalController?.abort(); state.modalController = null;
  }
  function openMethod(job, g, method) {
    stopEvidenceRequest();
    state.modal = { type: "method", job, g, method };
    $("run-detail").hidden = true; $("method-content").hidden = false; $("back-method").hidden = true;
    const group = job.analysis.groups[g], indexed = methodRuns(group, method);
    openDialog(`${method.estimator} · 种子列表`, `${jobName(job)} / ${groupName(group, g)}`, "METHOD COHORT");
    const container = $("method-content"); container.replaceChildren(referenceBlock(group), checksBlock(group), node("p", "hint", methodDescription(method)), node("p", "hint", `有估计 ${text(method.estimates_available)} / ${text(method.total_runs)} · 平均估计 ${percent(method.mean_estimate)} · 平均绝对误差 ${points(method.mean_absolute_error)}`), node("p", "hint", noScore(method, group)));
    if (indexed === null) {
      container.append(node("p", "notice error", "上游聚合缺少 target_cohort、budget_tier 或 strategy；为避免混合策略，未按方法 ID 猜测轮次。请从证据记录逐轮打开。"));
    } else {
      if (indexed.length !== method.total_runs) container.append(node("p", "notice error", `分组核对异常：匹配 ${indexed.length} 轮，上游分母 ${text(method.total_runs)}。保留原分母，不重算均值。`));
      container.append(table(runHeaders, runRows(job, g, indexed)));
    }
    const details = node("details"); details.append(node("summary", "", "上游方法聚合原值 / 完整策略与预算"), node("pre", "", pretty(method))); container.append(details);
    $("close-detail").focus({ preventScroll: true });
  }
  function openRun(job, g, r) {
    const back = state.modal?.type === "method" ? { job: state.modal.job, g: state.modal.g, method: state.modal.method } : null;
    stopEvidenceRequest();
    const run = job.analysis.groups[g].runs[r];
    state.modal = { type: "run", job, g, r, kind: "summary", page: 1, back };
    $("run-detail").hidden = false; $("method-content").hidden = true; $("back-method").hidden = !back;
    openDialog(`${run.estimator} · 种子 ${text(run.seed)}`, `${jobName(job)} / ${groupName(job.analysis.groups[g], g)} · 原索引 group=${g}, run=${r}`);
    loadEvidence("summary", 1).catch(showError);
    $("tab-summary").focus({ preventScroll: true });
  }
  async function openJob(id) {
    const job = await loadJob(id, true);
    stopEvidenceRequest(); state.modal = { type: "job", job };
    $("run-detail").hidden = true; $("method-content").hidden = false; $("back-method").hidden = true;
    openDialog(jobName(job), `${sourceName(job.source)} · ${job.id}`, "EXPERIMENT RECORD");
    const container = $("method-content");
    container.replaceChildren(badge(job.status), node("p", "hint", `已结束 ${text(job.finished_tasks)} / 计划 ${text(job.plan_count)} · 限时 ${text(job.time_limit_seconds)} 秒`));
    if (job.error) container.append(node("p", "feedback error", job.error));
    const metadata = node("dl", "metadata");
    [["输出目录", job.directory], ["结果清单", job.manifest || job.analysis?.manifest_path], ["参考路径", job.references], ["创建时间", dateLabel(job.created_at)]].forEach(([k, v]) => metadata.append(node("dt", "", k), node("dd", "path", v)));
    container.append(metadata, csvLinks(job));
    if (active(job) && job.source !== "published") container.append(cancelButton(job));
    container.append(button("刷新任务详情", () => openJob(id), "quiet"));
    container.append(node("h4", "", "完整输入配置"), node("pre", "", job.config == null ? "此记录未公开或未留存完整输入配置。" : pretty(job.config)), node("h4", "", "任务日志（服务端返回内容）"), node("pre", "", job.log || "尚未产生或未公开任务日志；空内容不表示任务成功。"));
  }
  function metadataList(entries) {
    const list = node("dl", "metadata");
    entries.forEach(([key, value]) => list.append(node("dt", "", key), node("dd", "", value)));
    return list;
  }
  function renderRunSummary(container, modal) {
    const group = modal.job.analysis.groups[modal.g], run = group.runs[modal.r];
    const head = node("div", "toolbar"); head.append(badge(run.status), badge(run.estimate_status), node("span", "hint", `工况核对：${labelStatus(run.target_status)}`));
    container.append(head);
    const stats = node("div", "detail-stats");
    [["原始失效概率估计", percent(run.pfail_estimate)], ["绝对误差", points(run.absolute_error)], ["log₁₀ 失效概率", text(run.log10_pfail)], ["种子", text(run.seed)]].forEach(([title, value]) => {
      const el = node("div", "detail-stat"); el.append(node("span", "", title), node("strong", "", value)); stats.append(el);
    });
    container.append(stats, referenceBlock(group), node("p", "hint", condition(group)));
    const cost = run.cost || {};
    const costs = node("div", "detail-stats");
    [["算法请求", cost.algorithm_requests], ["Xyce 提交", cost.xyce_submitted], ["Xyce 返回", cost.xyce_returned], ["正式估计分母", cost.formal_draws], ["墙钟耗时 / 秒", cost.wall_seconds]].forEach(([title, value]) => {
      const el = node("div", "detail-stat"); el.append(node("span", "", title), node("strong", "", text(value))); costs.append(el);
    });
    container.append(node("h4", "", "逐轮成本 · 原始口径"), costs);
    if (run.absolute_error == null) container.append(node("p", "notice subtle", "本轮未评分：参考不可用、目标不匹配或缺少可用线性估计等原因；以原始 estimate_status、target_status 及分析器判断为准。"));
    const warnings = [...new Set([...(run.warnings || []), ...(run.cost_issues || []), ...(run.plan_issues || []), ...(run.source?.issues || [])])];
    container.append(node("h4", "", "警告与检查"));
    if (warnings.length) { const list = node("ul", "warning-list"); warnings.forEach((w) => list.append(node("li", "", warningLabel(w)))); container.append(list); }
    else container.append(node("p", "hint", "上游未记录告警；这不等于额外的验证通过。"));
    container.append(metadataList([["轮次说明", run.detail || run.estimate_status], ["源码版本", run.source?.git_revision], ["源码有改动", run.source?.git_dirty], ["概要路径", run.source?.summary], ["概要 SHA-256", run.source?.summary_sha256], ["求解器 / 版本", `${text(run.source?.solver)} / ${text(run.source?.solver_version)}`], ["参考来源一致性", labelStatus(run.reference_source_match)], ["分析清单", modal.job.analysis.manifest_path], ["输出目录", modal.job.directory]]));
    const raw = node("details"); raw.append(node("summary", "", "分析器轮次原值（含目标、策略与来源）"), node("pre", "", pretty(run))); container.append(raw);
  }
  async function loadEvidence(kind, page = 1) {
    if (state.modal?.type !== "run") return;
    stopEvidenceRequest();
    const sequence = state.modalSequence, modal = state.modal;
    modal.kind = kind; modal.page = page;
    const controller = new AbortController(); state.modalController = controller;
    document.querySelectorAll("[data-kind]").forEach((tab) => {
      const selected = tab.dataset.kind === kind;
      tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
    });
    $("detail-panel").setAttribute("aria-labelledby", `tab-${kind}`);
    const container = $("detail-content"); container.replaceChildren();
    if (kind === "summary") renderRunSummary(container, modal);
    if (kind === "samples") container.append(node("p", "notice subtle", "样本为测量记录，非波形验证。按原字段展示失效 / 分类状态；缺失记录不等于零。每页 50 条。"));
    const loading = node("p", "hint", `正在读取${kindNames[kind]}文件…`); container.append(loading);
    container.setAttribute("aria-busy", "true");
    try {
      const data = await api(queryURL("/api/evidence", { id: modal.job.id, group: modal.g, run: modal.r, kind, page }), undefined, controller.signal);
      if (sequence !== state.modalSequence || !$("detail-dialog").open) return;
      loading.remove();
      const meta = node("div", "asset-meta");
      meta.append(node("p", "path", `文件路径：${data.path || "未提供"}`), node("p", "hint", `可用文件：${Array.isArray(data.assets) && data.assets.length ? data.assets.map((k) => kindNames[k] || k).join(" / ") : "未列出"}`));
      container.append(meta);
      if (!data.available) {
        container.append(node("p", "notice", `没有可读取的${kindNames[kind]}文件：可能未产生或未公开。${data.content ? " " + text(data.content) : ""} 不按零值解释。`));
        return;
      }
      const extension = kind === "samples" ? "csv" : kind === "log" ? "log" : kind === "report" ? "txt" : "json";
      const filename = String(data.path || "").split(/[\\/]/).pop() || `${kind}-g${modal.g}-r${modal.r}.${extension}`;
      if (data.assets?.includes(kind)) container.append(downloadLink(`下载${kindNames[kind]}原文件`, queryURL("/api/asset", { id: modal.job.id, group: modal.g, run: modal.r, kind }), filename));
      else container.append(node("p", "notice subtle", "以下为已保存的实验计划；本轮尚未产生可下载的原始附件。"));
      if (kind === "samples") {
        renderSamples(container, data);
        container.append(downloadLink("导出建模数据集（参数 → 测量）", queryURL("/api/dataset", { id: modal.job.id, group: modal.g, run: modal.r }), "dataset.csv"));
        container.append(node("p", "hint", "数据集把每批 samples.npy 的参数向量与 samples.csv 的测量/判定按 batch+xyce_run 配对，供后续代理模型使用；它不是评分参考。"));
      } else if (kind === "circuit") renderCircuit(container, data);
      else {
        if (kind === "log") container.append(node("p", "notice subtle", data.truncated ? "日志尾部节选：前部已截断。下载原文件查看完整内容。" : "日志原文。当前响应未标记尾部截断。"));
        else if (data.truncated) container.append(node("p", "notice subtle", "当前内容已截断，请下载原文件。"));
        const raw = node("pre", "", data.content == null || data.content === "" ? "文件可用，但当前响应内容为空；不解释为零或成功。" : pretty(data.content));
        if (kind === "summary") {
          const details = node("details"); details.append(node("summary", "", "原始概要文件"), raw); container.append(details);
        } else container.append(raw);
      }
    } catch (error) {
      if (sequence !== state.modalSequence || controller.signal.aborted) return;
      loading.remove();
      const message = node("div", "notice error", `读取${kindNames[kind]}失败 · ${errorText(error)}`);
      message.append(button("重试", () => loadEvidence(kind, page))); container.append(message);
    } finally {
      if (sequence === state.modalSequence) container.removeAttribute("aria-busy");
    }
  }
  function renderSamples(container, data) {
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const columns = Array.isArray(data.columns) && data.columns.length ? data.columns : [...new Set(rows.flatMap((row) => Object.keys(row)))];
    const page = Number(data.page) || 1, size = Number(data.page_size) || 50;
    const total = finite(data.total) ? data.total : null;
    const pages = total == null ? null : Math.max(1, Math.ceil(total / size));
    const pager = node("div", "pager");
    const prev = button("上一页", () => loadEvidence("samples", page - 1)); prev.disabled = page <= 1;
    const next = button("下一页", () => loadEvidence("samples", page + 1)); next.disabled = pages == null || page >= pages;
    pager.append(prev, node("span", "", `第 ${page} / ${text(pages)} 页 · 总计 ${text(total)} 条 · 本页 ${rows.length} 条`), next);
    container.append(pager);
    if (data.truncated) container.append(node("p", "hint", "响应为样本节选；使用分页查看其他记录，下载原文件保留完整字段。"));
    if (!rows.length) { container.append(node("p", "notice", "此页未返回样本记录；不据此判断失效率为零。")); return; }
    const columnNames = { sample: "样本编号 / sample", batch: "仿真批次 / batch", xyce_run: "批内编号 / xyce_run", read_delay_s: "读延迟 / read_delay_s（秒）", write_delay_s: "写延迟 / write_delay_s（秒）", hold_snm_v: "保持裕量 / hold_snm_v（伏）", read_snm_v: "读裕量 / read_snm_v（伏）", write_snm_v: "写裕量 / write_snm_v（伏）", outcome: "判定 / outcome" };
    const wrap = table(columns.map(key => columnNames[key] || key), rows.map((row) => columns.map((key) => {
      if (!Object.prototype.hasOwnProperty.call(row, key)) return "未提供";
      const value = row[key], raw = pretty(value);
      if (key === "outcome") return { pass: "通过（pass）", fail: "失效（fail）", unclassified: "未判定（unclassified）" }[value] || raw;
      if (value !== "" && finite(Number(value)) && /_delay_s$/.test(key)) return `${raw} s\n${Number((Number(value) * 1e9).toPrecision(9))} ns`;
      if (value !== "" && finite(Number(value)) && /_snm_v$/.test(key)) return `${raw} V\n${Number((Number(value) * 1000).toPrecision(9))} mV`;
      return raw;
    })), "sample-table");
    // Highlight only explicit failure booleans/labels, never infer failures from measurements.
    wrap.querySelectorAll("tbody tr").forEach((tr, i) => {
      const row = rows[i];
      if (["failed", "failure", "is_failure", "is_fail"].some((key) => [true, 1, "1", "true", "True"].includes(row[key])) || [row.status, row.outcome].some(value => ["failed", "failure", "fail"].includes(value))) tr.classList.add("sample-failure");
    });
    const raw = node("details");
    raw.append(node("summary", "", "本页样本原始 JSON"), node("pre", "", pretty(rows)));
    container.append(wrap, raw);
  }
  function renderCircuit(container, data) {
    const content = data.content || {};
    container.append(node("p", "notice subtle", content.note || "批次文件为仿真器原始产物。"));
    const batches = content.batches || [];
    if (!batches.length) { container.append(node("p", "notice", "该轮没有可展示的批次电路文件。")); return; }
    batches.forEach((batch) => {
      const card = node("section", "card");
      card.append(node("h4", "", `仿真批次 ${batch.name}`));
      if (batch.waveform) {
        const img = node("img", "waveform-img");
        img.src = batch.waveform; img.alt = `${batch.name} 波形图`; img.loading = "lazy";
        card.append(img, node("p", "hint", "波形图供人工核对位线/字线/内部节点行为；网页不做自动物理验收。"));
      }
      const links = node("div", "toolbar");
      if (batch.netlist) links.append(downloadLink("网表 .sp", batch.netlist, batch.netlist.split("name=")[1]));
      if (batch.variation) links.append(downloadLink("逐器件扰动 .json", batch.variation, batch.variation.split("name=")[1]));
      card.append(links, node("p", "path", `批次文件：${(batch.files || []).join("、")}`));
      container.append(card);
    });
  }

  function invalidate(message = "配置有改动，请重新校验。") {
    state.configRevision++; state.validated = null;
    $("validation-message").textContent = message; $("validation-message").className = "feedback";
    $("resolved-json").textContent = "上次校验已失效。"; $("plan-table").replaceChildren();
    $("form-state").textContent = state.jsonStaleForm ? "JSON 已改 · 表单待同步" : state.formDirty ? "表单有未写入改动" : "与 JSON 同步";
    syncButtons();
  }
  function restrictions() {
    const snm = $("operation").value.endsWith("snm");
    for (const id of ["rows", "cols", "target-row", "target-col", "mux", "equivalent"]) $(id).disabled = snm;
    if (snm) {
      $("rows").value = 1; $("cols").value = 1; $("target-row").value = 0; $("target-col").value = 0;
      $("mux").checked = false; $("equivalent").value = "0";
    }
    $("threshold-label").textContent = snm ? "裕量下限 / mV" : "延迟或输出周期上限 / ns";
    const onlyMC = $("variation").value !== "custom" || Number($("equivalent").value) !== 0;
    names.slice(1).forEach((name) => { $("alg-" + name).disabled = onlyMC; if (onlyMC) $("alg-" + name).checked = false; });
    const mixed = $("operation").value === "read" && $("alg-hscs").checked;
    $("mixed").disabled = !mixed; $("mixed-note").hidden = !mixed;
    if (!mixed) $("mixed").checked = false;
  }
  function assertConfig(config) {
    if (!config || !config.experiment || typeof config.experiment.case !== "string" || !Array.isArray(config.estimators) || !Array.isArray(config.seeds)) throw new Error("配置需包含 experiment.case、estimators 数组和 seeds 数组。");
  }
  function fill(config) {
    assertConfig(config);
    const e = config.experiment, c = e.circuit || {};
    $("cell").value = e.case.includes("10t") ? "10t" : "6t";
    $("operation").value = (c.operation || e.case.replace(/^sram_(6t|10t)_(?:\d+x\d+_)?/, "")).replace("read&write", "read_write");
    const values = { corner: e.corner || c.corner, vdd: e.vdd ?? c.vdd, temperature: e.temperature_c ?? c.temperature_c,
      std: e.relative_std, threshold: e.threshold_mv ?? e.threshold_ns, budget: config.budget_per_algorithm, "draw-cap": config.max_algorithm_draws, batch: e.batch_size };
    Object.entries(values).forEach(([id, value]) => { $(id).value = value ?? ""; });
    const array = c.array || [1, 1, false], target = c.target || [array[0] - 1, array[1] - 1];
    $("rows").value = array[0]; $("cols").value = array[1]; $("mux").checked = Boolean(array[2]);
    $("target-row").value = target[0]; $("target-col").value = target[1]; $("rc").checked = c.w_rc !== false;
    $("variation").value = c.variation_mode || "custom"; $("equivalent").value = c.real_cell_mode ?? 0;
    $("seeds").value = config.seeds.join(", ");
    names.forEach((name) => { $("alg-" + name).checked = config.estimators.some((profile) => profile.algorithm === name); });
    restrictions();
    $("mixed").checked = !$("mixed").disabled && config.estimators.some((profile) => profile.allow_hscs_mixed_search === true);
    const mixedValues = config.estimators.filter((p) => p.algorithm === "hscs").map((p) => Boolean(p.allow_hscs_mixed_search));
    $("mixed").indeterminate = !$("mixed").disabled && mixedValues.includes(true) && mixedValues.includes(false);
    $("profile-list").replaceChildren(...config.estimators.map((p, i) => node("p", "", `${i + 1}. ${p.id || p.name || p.algorithm} · ${p.algorithm} · ${p.mode || "默认模式"}`)));
    state.touched.clear();
  }
  function applyConfig(config) {
    assertConfig(config); fill(config);
    $("json").value = JSON.stringify(config, null, 2);
    state.formDirty = false; state.jsonStaleForm = false;
    invalidate("配置已载入，尚未校验；不会自动运行。");
  }
  function readConfig() { return JSON.parse($("json").value); }
  function number(id) {
    if (!$(id).value.trim() || !Number.isFinite(Number($(id).value))) throw new Error(`请填写有效数值：${$(id).closest("label")?.textContent.trim() || id}`);
    return Number($(id).value);
  }
  function writeForm(event) {
    event.preventDefault();
    try {
      if (state.jsonStaleForm) throw new Error("JSON 已改动。请先点击“JSON 同步到表单”，避免旧表单覆盖新 JSON。");
      restrictions();
      const config = readConfig(); assertConfig(config);
      const e = config.experiment, c = e.circuit || {}, operation = $("operation").value, cell = $("cell").value;
      const previousVariation = c.variation_mode || "custom";
      if (state.touched.has("cell") || state.touched.has("operation")) e.case = `sram_${cell}_${operation}`;
      e.corner = $("corner").value;
      e.vdd = number("vdd"); e.temperature_c = number("temperature"); e.relative_std = number("std");
      delete e.threshold_mv; delete e.threshold_ns;
      e[operation.endsWith("snm") ? "threshold_mv" : "threshold_ns"] = number("threshold");
      e.batch_size = number("batch"); e.u0_positive = $("variation").value === "custom";
      e.circuit = { ...c, corner: e.corner, vdd: e.vdd, temperature_c: e.temperature_c,
        cell_type: `SRAM_${cell.toUpperCase()}_CELL`, operation: operation === "read_write" ? "read&write" : operation,
        array: [number("rows"), number("cols"), $("mux").checked], target: [number("target-row"), number("target-col")],
        variation_mode: $("variation").value, real_cell_mode: number("equivalent"), w_rc: $("rc").checked };
      if (cell === "10t") e.circuit.cell_6t = null;
      const linkedDrawCap = config.max_algorithm_draws === config.budget_per_algorithm && !state.touched.has("draw-cap");
      config.budget_per_algorithm = number("budget");
      config.max_algorithm_draws = state.touched.has("budget") && linkedDrawCap ? config.budget_per_algorithm : number("draw-cap");
      if (!$("seeds").value.trim()) throw new Error("请填写至少一个种子。");
      config.seeds = $("seeds").value.split(/[,，]/).map((part) => {
        const value = Number(part.trim());
        if (!part.trim() || !Number.isSafeInteger(value) || value < 0) throw new Error("种子须为非负安全整数，使用逗号分隔。");
        return value;
      });
      const selected = names.filter((name) => $("alg-" + name).checked);
      // Filter existing profiles in place-order; never replace a strategy family with one template.
      const profiles = config.estimators.filter((profile) => !names.includes(profile.algorithm) || selected.includes(profile.algorithm));
      selected.forEach((name) => {
        if (!profiles.some((p) => p.algorithm === name)) {
          const defaultProfile = state.context.template.estimators.find((p) => p.algorithm === name);
          if (!defaultProfile) throw new Error(`模板未提供 ${name} 策略。`);
          const added = clone(defaultProfile);
          if (added.id && profiles.some((p) => p.id === added.id)) {
            let i = 2; while (profiles.some((p) => p.id === `${added.id}-${i}`)) i++;
            added.id = `${added.id}-${i}`;
          }
          profiles.push(added);
        }
      });
      config.estimators = profiles.map((profile) => {
        const copy = clone(profile);
        if (copy.algorithm === "mc" && (previousVariation !== $("variation").value || !copy.mode)) copy.mode = e.u0_positive ? "reference" : "legacy";
        // Unrelated budget edits must not flatten per-profile HSCS settings.
        if (copy.algorithm === "hscs" && (state.touched.has("mixed") || $("mixed").disabled)) copy.allow_hscs_mixed_search = $("mixed").checked;
        return copy;
      });
      applyConfig(config);
      $("validation-message").textContent = "表单已写入 JSON，全部保留策略按原顺序提交。请校验当前配置。";
    } catch (error) { feedback(errorText(error)); }
  }
  async function validateConfig() {
    if (state.validating) return;
    state.validating = true; syncButtons();
    const submittedText = $("json").value, submittedRevision = state.configRevision, contextRevision = state.context?.revision;
    try {
      if (state.formDirty) throw new Error("表单有未写入改动，请先点击“将表单写入 JSON”。");
      const result = await api("/api/validate", { config: JSON.parse(submittedText) });
      if (submittedRevision !== state.configRevision || submittedText !== $("json").value || state.formDirty || contextRevision !== state.context?.revision) {
        feedback("校验期间输入已变化，已忽略旧响应。请重新校验当前配置。"); return;
      }
      fill(result.config); state.jsonStaleForm = false;
      state.validated = { text: submittedText, revision: submittedRevision, contextRevision };
      $("form-state").textContent = "已校验 · 表单为规范化值";
      $("resolved-json").textContent = pretty(result.config);
      $("plan-table").replaceChildren(table(["方法策略", "算法", "种子", "预算"], (result.plan || []).map((row) => [row.estimator, row.algorithm, row.seed, row.budget])));
      feedback(`校验通过 · ${text(result.tasks)} 条任务 · ${text(result.sample_cap)} 条预算样本上限\n${result.note || ""}`, true);
    } catch (error) {
      if (submittedRevision === state.configRevision) { state.validated = null; feedback(errorText(error)); }
      else showError(`旧配置校验失败（当前输入未覆盖）· ${errorText(error)}`);
    } finally { state.validating = false; syncButtons(); }
  }
  async function acceptJob(job, select = true) {
    if (!job?.id) throw new Error("任务响应缺少 id。请刷新历史确认提交状态，勿重复运行。");
    const existing = state.jobs.findIndex((j) => j.id === job.id);
    if (existing >= 0) state.jobs[existing] = { ...state.jobs[existing], ...job }; else state.jobs.unshift(job);
    if (job.analysis) state.details.set(job.id, job);
    if (select) { state.scope = job.id; state.source = "all"; $("source-filter").value = "all"; }
    syncScope(); $("scope").value = state.scope;
    syncButtons(); schedulePoll(); renderView();
    try { await refreshJobs(); } catch (error) { showError(`任务已接收，但历史读取失败 · ${errorText(error)}`); }
  }
  async function runConfig() {
    if (state.runPending || $("run").disabled) return;
    state.runPending = true; $("run").disabled = true; syncButtons();
    try {
      const valid = state.validated;
      if (!valid || valid.text !== $("json").value || valid.revision !== state.configRevision || valid.contextRevision !== state.context?.revision || state.formDirty) throw new Error("请先校验当前 JSON。");
      if (state.jobs.some(active)) throw new Error("当前有活动任务，不支持排队。");
      const limit = number("time-limit");
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 3600) throw new Error("整场限时须为 1–3600 秒的整数。");
      const payload = { name: $("experiment-name").value.trim(), config: JSON.parse(valid.text), references: $("references").value.split("\n").map((s) => s.trim()).filter(Boolean), time_limit_seconds: limit };
      // Consume validation before submitting: a timeout is not permission to repeat a run.
      state.validated = null;
      const job = await api("/api/run", payload);
      await acceptJob(job); navigate("overview");
      feedback("任务已提交。再次运行前请重新校验。", true);
    } catch (error) {
      feedback(`运行请求未确认成功 · ${errorText(error)}\n请刷新实验历史核对状态；不会自动重试。`);
      showError(error);
    } finally { state.runPending = false; syncButtons(); }
  }
  async function analyzeManifest(manifest, name) {
    if (!manifest.trim()) throw new Error("请填写已有结果清单的完整路径。");
    if (busy()) throw new Error("当前有任务正在处理，请结束后再载入证据。不会加入队列。");
    state.analyzePending = true; syncButtons();
    try {
      const payload = { manifest: manifest.trim() }; if (name) payload.name = name;
      const job = await api("/api/analyze", payload);
      await acceptJob(job);
      navigate(job.analysis || job.analysis_available ? "evidence" : "overview");
    } finally { state.analyzePending = false; syncButtons(); }
  }
  function suiteInvalidate(message) {
    state.suiteRevision++; state.suiteValidated = null;
    $("suite-message").textContent = message || "套件有改动，请重新校验。";
    $("suite-message").className = "feedback";
    $("suite-plan").replaceChildren(); syncButtons();
  }
  async function validateSuite() {
    if (state.suiteValidating) return;
    state.suiteValidating = true; syncButtons();
    const text = $("suite-json").value, revision = state.suiteRevision;
    try {
      const parsed = JSON.parse(text);
      const experiments = Array.isArray(parsed) ? parsed : parsed.experiments;
      if (!Array.isArray(experiments)) throw new Error("套件须为配置数组，或 {experiments:[...]} 对象。");
      const result = await api("/api/suite/validate", { experiments });
      if (revision !== state.suiteRevision || text !== $("suite-json").value) {
        $("suite-message").textContent = "校验期间套件已变化，已忽略旧响应。请重新校验。"; return;
      }
      state.suiteValidated = { text, experiments };
      $("suite-plan").replaceChildren(table(["电路工况", "轮数", "仿真上限"], result.plans.map((p) => [p.label, p.tasks, p.sample_cap])));
      $("suite-message").textContent = `套件校验通过 · ${result.tasks} 轮 · 合计上限 ${result.sample_cap} 次仿真`;
      $("suite-message").className = "feedback success";
    } catch (error) {
      state.suiteValidated = null;
      $("suite-message").textContent = errorText(error); $("suite-message").className = "feedback error";
    } finally { state.suiteValidating = false; syncButtons(); }
  }
  async function runSuite() {
    if (state.suiteRunPending || $("suite-run").disabled) return;
    state.suiteRunPending = true; syncButtons();
    try {
      const valid = state.suiteValidated;
      if (!valid || valid.text !== $("suite-json").value) throw new Error("请先校验当前套件。");
      if (busy()) throw new Error("当前有活动任务，不支持排队。");
      const limit = Number($("suite-time-limit").value);
      if (!Number.isInteger(limit) || limit < 1 || limit > 3600) throw new Error("套件整轮限时须为 1–3600 秒的整数。");
      state.suiteValidated = null;
      const job = await api("/api/suite/run", { name: "多电路套件", experiments: valid.experiments, time_limit_seconds: limit });
      await acceptJob(job); navigate("overview");
      $("suite-message").textContent = "套件已提交；再次运行前请重新校验。"; $("suite-message").className = "feedback success";
    } catch (error) {
      $("suite-message").textContent = `套件请求未确认成功 · ${errorText(error)}；请刷新历史核对，勿重复提交。`;
      $("suite-message").className = "feedback error"; showError(error);
    } finally { state.suiteRunPending = false; syncButtons(); }
  }
  function renderCatalog() {
    const list = $("catalog-list"); list.replaceChildren();
    const catalog = state.context?.catalog || [];
    if (!catalog.length) list.append(node("p", "hint", "未提供快捷证据目录，可手动填写清单路径。"));
    catalog.forEach((entry) => {
      const el = node("div", "catalog-item"), info = node("div");
      info.append(node("strong", "", entry.label || entry.manifest), node("p", "path", entry.manifest), node("span", "badge", sourceName(entry.source)));
      const load = button("载入证据", () => analyzeManifest(entry.manifest, entry.label)); load.dataset.catalogImport = "true";
      el.append(info, load); list.append(el);
    });
    syncButtons();
  }
  async function initialize() {
    if (state.loading) return;
    state.loading = true; $("retry").disabled = true;
    try {
      const context = await api("/api/context");
      if (!context.token || !context.template) throw new Error("上下文缺少 token 或原版模板。");
      if (state.context && context.revision !== state.context.revision) invalidate("后端版本已变化，请重新校验。");
      state.context = context;
      if (!state.initializedForm) {
        applyConfig(context.presets?.[0]?.config || context.template);
        $("references").value = context.reference || "";
        state.initializedForm = true;
      }
      const oldPreset = $("preset").value;
      $("preset").replaceChildren(...(context.presets || []).map((p, i) => new Option(p.name || p.id, String(i))), new Option("原版五算法模板", "template"));
      if ([...$("preset").options].some((o) => o.value === oldPreset)) $("preset").value = oldPreset;
      ["preset", "apply-preset", "settings-fields", "import", "export", "fill-form", "json"].forEach((id) => { $(id).disabled = false; });
      state.ready = true;
      $("revision").textContent = `版本 ${context.revision || "未记录"}`; $("revision").title = `输出根目录：${context.output_root || "未记录"}`;
      renderCatalog(); renderView(); syncButtons();
      await refreshJobs(true);
      $("global-message").hidden = state.detailErrors.size === 0;
    } catch (error) {
      showError(`初始化 / 历史加载失败 · ${errorText(error)}`);
      $("connection").textContent = "连接异常"; $("connection").className = "badge bad";
    } finally { state.loading = false; $("retry").disabled = false; syncButtons(); }
  }

  names.forEach((name) => {
    const label = node("label", "check"), input = node("input"); input.type = "checkbox"; input.id = `alg-${name}`;
    label.append(input, document.createTextNode(name.toUpperCase())); $("algorithms").append(label);
  });
  document.querySelectorAll("[data-view]").forEach((control) => control.addEventListener("click", () => navigate(control.dataset.view)));
  document.querySelectorAll("[data-import]").forEach((control) => control.addEventListener("click", goImport));
  window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
  listen("scope", "change", async () => {
    state.scope = $("scope").value; renderView();
    if (state.scope !== "all") { await loadJob(state.scope); renderView(); }
  });
  listen("source-filter", "change", () => { state.source = $("source-filter").value; renderView(); });
  listen("search", "input", () => { state.query = $("search").value.trim().toLowerCase(); renderView(); });
  listen("refresh", "click", () => state.ready ? refreshJobs(true) : initialize());
  listen("retry", "click", initialize);
  listen("dismiss-message", "click", () => { $("global-message").hidden = true; });
  document.addEventListener("visibilitychange", () => { schedulePoll(); });
  listen("settings", "input", (event) => {
    state.formDirty = true; state.touched.add(event.target.id); restrictions(); invalidate();
  });
  listen("settings", "change", (event) => {
    state.formDirty = true; state.touched.add(event.target.id); restrictions(); invalidate();
  });
  listen("settings", "submit", writeForm);
  listen("json", "input", () => {
    state.jsonStaleForm = true; invalidate("JSON 已改动，校验已失效。可直接校验 JSON；继续编辑表单前请先同步。");
  });
  ["experiment-name", "references", "time-limit"].forEach((id) => listen(id, "input", () => invalidate()));
  listen("fill-form", "click", () => {
    try {
      fill(readConfig()); state.formDirty = false; state.jsonStaleForm = false; invalidate("JSON 已同步到表单，请校验当前配置。");
    } catch (error) { feedback(errorText(error)); }
  });
  listen("apply-preset", "click", () => {
    if ((state.formDirty || state.jsonStaleForm) && !window.confirm("应用预设将替换当前未保存的表单和 JSON，是否继续？")) return;
    const selected = $("preset").value;
    const preset = selected === "template" ? null : state.context.presets[Number(selected)];
    if (preset && preset.suite) {
      $("suite-json").value = JSON.stringify(preset.suite.map((c) => ({ config: c })), null, 2);
      suiteInvalidate("套件预设已填入下方套件输入；单电路表单未改动。");
      $("suite-panel").scrollIntoView({ block: "start" });
      return;
    }
    applyConfig(selected === "template" ? state.context.template : preset.config);
  });
  listen("suite-json", "input", () => suiteInvalidate());
  listen("suite-validate", "click", validateSuite);
  listen("suite-run", "click", runSuite);
  listen("suite-example", "click", () => {
    const preset = (state.context?.presets || []).find((p) => p.id === "suite");
    if (!preset) return showError("当前上下文没有套件示例。");
    $("suite-json").value = JSON.stringify(preset.suite.map((c) => ({ config: c })), null, 2);
    suiteInvalidate("双电路示例已载入，尚未校验；不会自动运行。");
  });
  listen("import", "change", async (event) => {
    const file = event.target.files?.[0]; if (!file) return;
    invalidate("正在导入 JSON…"); const revision = state.configRevision;
    try {
      const config = JSON.parse(await file.text());
      if (revision !== state.configRevision) { feedback("导入期间输入已变化，已忽略文件内容。请重新选择文件。"); return; }
      applyConfig(config);
    } catch (error) { feedback(`导入失败 · ${errorText(error)}`); }
    finally { event.target.value = ""; }
  });
  listen("export", "click", () => {
    try {
      if (state.formDirty) throw new Error("表单有未写入改动，请先写入 JSON 再导出。");
      saveBlob(new Blob([JSON.stringify(readConfig(), null, 2)], { type: "application/json" }), "experiment.json");
    } catch (error) { feedback(errorText(error)); }
  });
  listen("validate", "click", validateConfig);
  listen("run", "click", runConfig);
  listen("analyze-form", "submit", (event) => { event.preventDefault(); return analyzeManifest($("manifest").value); });
  listen("close-detail", "click", () => $("detail-dialog").close());
  listen("detail-dialog", "close", () => {
    stopEvidenceRequest(); state.modal = null;
    const target = state.modalReturn?.isConnected ? state.modalReturn : $("page-title");
    if (!target.hasAttribute("tabindex") && target === $("page-title")) target.tabIndex = -1;
    target.focus({ preventScroll: true });
  });
  listen("back-method", "click", () => {
    const back = state.modal?.back; if (back) openMethod(back.job, back.g, back.method);
  });
  document.querySelectorAll("[data-kind]").forEach((tab) => {
    tab.addEventListener("click", () => loadEvidence(tab.dataset.kind).catch(showError));
    tab.addEventListener("keydown", (event) => {
      const index = kinds.indexOf(tab.dataset.kind); let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % kinds.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + kinds.length) % kinds.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = kinds.length - 1;
      else return;
      event.preventDefault(); $("tab-" + kinds[next]).focus(); loadEvidence(kinds[next]).catch(showError);
    });
  });
  navigate(location.hash.slice(1));
  initialize().catch(showError);
})();
