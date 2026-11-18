"use strict";
const $ = (id) => document.getElementById(id);
let token,
  template,
  validatedText = null,
  activeJob = null,
  formDirty = false,
  configRevision = 0;
const names = ["mc", "mnis", "ais", "acs", "hscs"];
const readConfig = () => JSON.parse($("json").value);
const number = (id) => Number($(id).value);
const errorText = (e) => e.message || String(e);
const percent = (n) =>
  n === null || n === undefined ? "—" : `${(n * 100).toPrecision(5)}%`;
function feedback(message, good = false) {
  $("validation-message").textContent = message;
  $("validation-message").className = `feedback ${good ? "success" : "error"}`;
}
function invalidate() {
  configRevision += 1;
  validatedText = null;
  $("run").disabled = true;
  $("validation-message").textContent = "配置有改动，请重新校验。";
  $("validation-message").className = "feedback";
}
async function api(path, data) {
  const options =
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-OpenYield-Token": token,
          },
          body: JSON.stringify(data),
        };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || `请求失败 ${response.status}`);
  return result;
}
function restrictions() {
  const snm = $("operation").value.endsWith("snm");
  for (const id of ["rows", "cols", "target-row", "target-col", "mux"])
    $(id).disabled = snm;
  if (snm) {
    $("rows").value = 1;
    $("cols").value = 1;
    $("target-row").value = 0;
    $("target-col").value = 0;
    $("mux").checked = false;
    $("equivalent").value = "0";
  }
  $("equivalent").disabled = snm;
  $("threshold-label").textContent = snm
    ? "裕量下限 / mV"
    : "延迟或输出周期上限 / ns";
  const onlyMC =
    $("variation").value !== "custom" || number("equivalent") !== 0;
  for (const name of names.slice(1)) {
    $(`alg-${name}`).disabled = onlyMC;
    if (onlyMC) $(`alg-${name}`).checked = false;
  }
  const mixed = $("operation").value === "read" && $("alg-hscs").checked;
  $("mixed").disabled = !mixed;
  $("mixed-note").hidden = !mixed;
  if (!mixed) $("mixed").checked = false;
}
function fill(config) {
  const e = config.experiment,
    c = e.circuit || {};
  $("cell").value = e.case.includes("10t") ? "10t" : "6t";
  $("operation").value = e.case.replace(/^sram_(6t|10t)_/, "");
  for (const [id, value] of Object.entries({
    corner: e.corner || c.corner,
    vdd: e.vdd,
    temperature: e.temperature_c,
    std: e.relative_std,
    threshold: e.threshold_mv ?? e.threshold_ns,
    budget: config.budget_per_algorithm,
    "draw-cap": config.max_algorithm_draws,
    batch: e.batch_size,
  }))
    $(id).value = value;
  const array = c.array || [1, 1, false],
    target = c.target || [array[0] - 1, array[1] - 1];
  $("rows").value = array[0];
  $("cols").value = array[1];
  $("mux").checked = array[2];
  $("target-row").value = target[0];
  $("target-col").value = target[1];
  $("rc").checked = c.w_rc !== false;
  $("variation").value = c.variation_mode || "custom";
  $("equivalent").value = c.real_cell_mode ?? 0;
  $("seeds").value = config.seeds.join(", ");
  for (const name of names)
    $(`alg-${name}`).checked = config.estimators.some(
      (x) => x.algorithm === name,
    );
  restrictions();
  $("mixed").checked = config.estimators.some(
    (x) => x.allow_hscs_mixed_search === true,
  );
}
$("settings").addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    const config = readConfig(),
      e = config.experiment,
      c = e.circuit || {};
    const operation = $("operation").value,
      snm = operation.endsWith("snm"),
      cell = $("cell").value;
    e.case = `sram_${cell}_${operation}`;
    e.corner = $("corner").value;
    e.vdd = number("vdd");
    e.temperature_c = number("temperature");
    e.relative_std = number("std");
    delete e.threshold_mv;
    delete e.threshold_ns;
    e[snm ? "threshold_mv" : "threshold_ns"] = number("threshold");
    e.batch_size = number("batch");
    e.u0_positive = $("variation").value === "custom";
    e.circuit = {
      ...c,
      corner: e.corner,
      vdd: e.vdd,
      temperature_c: e.temperature_c,
      cell_type: `SRAM_${cell.toUpperCase()}_CELL`,
      operation: operation === "read_write" ? "read&write" : operation,
      array: [number("rows"), number("cols"), $("mux").checked],
      target: [number("target-row"), number("target-col")],
      variation_mode: $("variation").value,
      real_cell_mode: number("equivalent"),
      w_rc: $("rc").checked,
    };
    if (cell === "10t") e.circuit.cell_6t = null;
    config.budget_per_algorithm = number("budget");
    config.max_algorithm_draws = number("draw-cap");
    if (!$("seeds").value.trim()) throw new Error("请填写至少一个种子。");
    config.seeds = $("seeds")
      .value.split(",")
      .map((s) => {
        if (!s.trim()) throw new Error("种子之间请用逗号分隔。");
        return Number(s.trim());
      });
    const selected = names.filter((name) => $("alg-" + name).checked);
    const profiles = config.estimators.filter((profile) =>
      selected.includes(profile.algorithm),
    );
    for (const name of selected) {
      if (!profiles.some((profile) => profile.algorithm === name)) {
        profiles.push(template.estimators.find((profile) => profile.algorithm === name));
      }
    }
    config.estimators = profiles.map((profile) => {
      const copy = structuredClone(profile);
      if (copy.algorithm === "mc") {
        copy.mode = e.u0_positive ? "reference" : "legacy";
      }
      if (copy.algorithm === "hscs") {
        copy.allow_hscs_mixed_search = $("mixed").checked;
      }
      return copy;
    });
    $("json").value = JSON.stringify(config, null, 2);
    formDirty = false;
    invalidate();
  } catch (e) {
    feedback(errorText(e));
  }
});
for (const id of ["operation", "variation", "equivalent"])
  $(id).addEventListener("change", restrictions);
$("settings").addEventListener("input", () => {
  formDirty = true;
  invalidate();
});
$("json").addEventListener("input", invalidate);
$("validate").onclick = async () => {
  $("validate").disabled = true;
  try {
    if (formDirty) throw new Error("表单有改动，请先点击“将表单写入 JSON”。");
    const submittedText = $("json").value;
    const submittedRevision = configRevision;
    const result = await api("/api/validate", {
      config: JSON.parse(submittedText),
    });
    if (
      submittedRevision !== configRevision ||
      submittedText !== $("json").value ||
      formDirty
    ) {
      invalidate();
      feedback("校验期间配置已变化，请重新校验当前输入。");
      return;
    }
    fill(result.config);
    validatedText = submittedText;
    $("run").disabled = Boolean(activeJob);
    $("resolved-json").textContent = JSON.stringify(result.config, null, 2);
    feedback(
      `配置检查通过 · ${result.tasks} 条任务 · ${result.sample_cap} 条预算样本上限\n${result.note}`,
      true,
    );
  } catch (e) {
    invalidate();
    feedback(errorText(e));
  } finally {
    $("validate").disabled = false;
  }
};
$("run").onclick = async () => {
  try {
    if (formDirty || $("json").value !== validatedText)
      throw new Error("请先校验当前 JSON。");
    const references = $("references")
      .value.split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    const job = await api("/api/run", {
      config: readConfig(),
      references,
      time_limit_seconds: number("time-limit"),
    });
    watch(job.id);
  } catch (e) {
    feedback(errorText(e));
  }
};
$("analyze").onclick = async () => {
  try {
    const job = await api("/api/analyze", {
      manifest: $("manifest").value.trim(),
    });
    watch(job.id);
  } catch (e) {
    $("job-state").textContent = errorText(e);
  }
};
function table(headers, rows) {
  const wrapper = document.createElement("div");
  wrapper.className = "table-wrap";
  const t = document.createElement("table"),
    head = t.createTHead().insertRow();
  for (const h of headers) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = h;
    head.append(th);
  }
  const body = t.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const value of row) {
      const td = tr.insertCell();
      td.textContent = value ?? "—";
    }
  }
  wrapper.append(t);
  return wrapper;
}
function render(analysis) {
  $("result-content").replaceChildren();
  const source = document.createElement("p");
  source.className = "path";
  source.textContent = `结果来自清单 ${analysis.manifest_path}`;
  $("result-content").append(source);
  for (const group of analysis.groups) {
    const anchor = group.runs.find((row) => row.target_status === "same");
    const identity = document.createElement("p");
    identity.className = "result-note";
    if (anchor) {
      const target = anchor.target;
      const circuit = target.resolved_circuit;
      const unit = target.threshold_field === "threshold_mv" ? "mV" : "ns";
      identity.textContent = `结果工况 · ${circuit.cell_type} · ${circuit.array[0]}×${circuit.array[1]} · ${target.operation} · ${circuit.corner} · ${circuit.vdd} V · ${circuit.temperature_c} ℃ · 门限 ${target.threshold} ${unit}`;
      const revision = document.createElement("p");
      revision.className = "path";
      revision.textContent = `基准记录源码 ${anchor.source.git_revision || "未记录"} · 目标单元 [${target.target.join(", ")}]`;
      $("result-content").append(identity, revision);
    } else {
      identity.textContent = "这组记录没有通过校验的目标定义，请检查结果清单。";
      $("result-content").append(identity);
    }
    const note = document.createElement("p");
    note.className = "result-note";
    const ref = group.reference;
    note.textContent =
      ref.status === "available"
        ? `独立参考 ${ref.failure_count}/${ref.samples} · ${percent(ref.estimate)} · 95% 区间 ${ref.interval.map(percent).join(" – ")}`
        : "独立参考缺失或未通过检查，误差栏留空。";
    $("result-content").append(note);
    const checks = document.createElement("p");
    checks.className = "hint";
    checks.textContent = `${group.checks.total_runs} 条记录 · 同目标 ${group.checks.target_same} · 目标偏离 ${group.checks.target_different} · 来源不完整 ${group.checks.source_incomplete} · 计划偏离 ${group.checks.plan_mismatch}`;
    $("result-content").append(
      checks,
      table(
        [
          "方法",
          "有估计 / 计划",
          "无估计",
          "低权重告警",
          "平均估计",
          "平均绝对误差",
        ],
        group.methods.map((m) => [
          m.estimator,
          `${m.estimates_available} / ${m.total_runs}`,
          m.no_estimate_runs,
          m.weight_warning_runs,
          percent(m.mean_estimate),
          m.mean_absolute_error == null
            ? "—"
            : `${(m.mean_absolute_error * 100).toPrecision(4)} 个百分点`,
        ]),
      ),
    );
    $("result-content").append(
      table(
        [
          "方法",
          "种子",
          "状态",
          "原始估计",
          "log₁₀ 估计",
          "请求 / 提交 / 返回",
          "正式分母",
          "告警",
          "误差 / 百分点",
          "明细",
        ],
        group.runs.map((r) => [
          r.estimator,
          r.seed,
          r.status,
          percent(r.pfail_estimate),
          r.log10_pfail,
          `${r.cost.algorithm_requests} / ${r.cost.xyce_submitted} / ${r.cost.xyce_returned}`,
          r.cost.formal_draws,
          r.warnings.join(", ") || "—",
          r.absolute_error == null
            ? "—"
            : (r.absolute_error * 100).toPrecision(4),
          r.detail || r.estimate_status,
        ]),
      ),
    );
  }
}
async function watch(id) {
  activeJob = id;
  $("run").disabled = true;
  $("analyze").disabled = true;
  $("result-content").replaceChildren();
  $("download-links").replaceChildren();
  try {
    const job = await api(`/api/job?id=${encodeURIComponent(id)}`);
    $("job-directory").textContent = job.directory;
    $("run-log").textContent = job.log || "读取结果中…";
    const state = {
      running: "运行中",
      complete: "任务结束",
      incomplete: "任务未完整完成",
      timeout: "达到时间限制",
      error: "任务报错",
    }[job.status];
    $("job-state").textContent =
      `${state}${job.finished_tasks == null ? "" : ` · 已结束 ${job.finished_tasks} 条`}${job.error ? ` · ${job.error}` : ""}`;
    if (job.analysis) {
      render(job.analysis);
      for (const name of [
        "runs.csv",
        "methods.csv",
        "groups.csv",
        "references.csv",
      ]) {
        const a = document.createElement("a");
        a.textContent = name;
        a.href = `/api/download?id=${id}&name=${name}`;
        a.download = name;
        $("download-links").append(a);
      }
    }
    if (job.status === "running") {
      setTimeout(() => watch(id), 1800);
      return;
    }
  } catch (e) {
    $("job-state").textContent = `读取任务失败 · ${errorText(e)}`;
  }
  activeJob = null;
  $("analyze").disabled = false;
  $("run").disabled = formDirty || $("json").value !== validatedText;
}
$("import").onchange = async (event) => {
  try {
    const file = event.target.files[0];
    if (!file) return;
    const config = JSON.parse(await file.text());
    $("json").value = JSON.stringify(config, null, 2);
    fill(config);
    formDirty = false;
    invalidate();
  } catch (e) {
    feedback(`导入失败 · ${errorText(e)}`);
  }
};
$("export").onclick = () => {
  try {
    const blob = new Blob([JSON.stringify(readConfig(), null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "experiment.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    feedback(errorText(e));
  }
};
async function initialize() {
  try {
    const context = await api("/api/context");
    token = context.token;
    template = context.template;
    for (const name of names) {
      const label = document.createElement("label");
      label.className = "check";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.id = `alg-${name}`;
      input.checked = true;
      input.onchange = restrictions;
      label.append(input, name.toUpperCase());
      $("algorithms").append(label);
    }
    $("json").value = JSON.stringify(template, null, 2);
    fill(template);
  } catch (e) {
    feedback(`页面初始化失败 · ${errorText(e)}`);
  }
}
initialize();
