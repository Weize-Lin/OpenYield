"""OpenYield evaluation workbench.

Full mode drives the upstream OpenYield experiment/analysis entry points.
Demo mode (--demo DIR) serves a bundled, read-only evidence package with the
Python standard library only, so the same UI can be shared without Xyce.
"""
import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

ROOT = Path(os.environ.get("OPENYIELD_REPOSITORY", Path(__file__).resolve().parents[2])).resolve()
STATIC = Path(__file__).with_name("public")
TERMINATION_GRACE_SECONDS = 30
_backend_revision = None
TABLES = ("runs.csv", "methods.csv", "groups.csv", "references.csv")
BATCH_SUFFIXES = {".png", ".sp", ".json", ".csv", ".log"}
OP_NAMES = {"hold_snm": "保持裕量", "read_snm": "读裕量", "write_snm": "写裕量",
            "read": "读延迟", "write": "写延迟", "read&write": "读写周期"}
_upstream = None


def upstream():
    global _upstream, _backend_revision
    if _upstream is None:
        sys.path.insert(0, str(ROOT))
        try:
            from yield_estimation.evaluation import analyze_results, experiment
            from yield_estimation.evaluation import compatibility
        except ImportError as exc:
            raise ValueError("后端接口或依赖不可用，请使用集成分支并核对 Python 环境：" + str(exc)) from exc
        for module in (analyze_results, experiment, compatibility):
            if ROOT not in Path(module.__file__).resolve().parents:
                raise ValueError("后端模块来自其他目录，请重新启动并核对 OPENYIELD_REPOSITORY。")
        if not all(callable(value) for value in (analyze_results.analyze, experiment.normalize_experiment, experiment.run_experiment)):
            raise ValueError("后端缺少可调用的实验或分析入口。")
        required = {"normalize_experiment", "run_experiment", "analyze", "frozen_suite_v1"}
        if compatibility.API_VERSION != 1 or not required <= compatibility.CAPABILITIES:
            raise ValueError("后端缺少工作台所需的软件接口，请使用集成分支，不要退回旧算法。")
        _backend_revision = source_snapshot()["git_revision"]
        _upstream = (analyze_results, experiment)
    return _upstream


def source_snapshot():
    revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True)
    tracked = any(line and not line.startswith("??") for line in status.splitlines())
    return dict(repository=str(ROOT), git_revision=revision, git_tracked_files_modified=tracked,
                git_status=status, api_version=1)


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_table(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
                             if isinstance(value, (list, dict)) else value for key, value in row.items()})


def contained(path, roots):
    path = Path(path).expanduser().resolve()
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("路径必须位于已登记的实验输出目录内。")
    return path


def preview(config):
    _, experiment = upstream()
    normalized = experiment.normalize_experiment(config)
    tasks = len(normalized["estimators"]) * len(normalized["seeds"])
    result = dict(config=normalized, tasks=tasks, sample_cap=tasks * normalized["budget_per_algorithm"],
                  note="配置通过程序检查。电路可运行性、门限依据和估计精度需要实验验证。")
    result["plan"] = [dict(estimator=p["id"], algorithm=p["algorithm"], seed=seed,
                           budget=normalized["budget_per_algorithm"])
                      for p in normalized["estimators"] for seed in normalized["seeds"]]
    return result


def circuit_label(config):
    c = config["experiment"].get("circuit", {})
    array = c.get("array", [1, 1])
    return f"{array[0]}×{array[1]} · {OP_NAMES.get(c.get('operation'), c.get('operation', '未知操作'))}"


def read_config():
    return dict(schema_version=1, experiment=dict(
        case="sram_6t_3x2_read", corner="TT", vdd=1, temperature_c=27, relative_std=.05, u0_positive=True,
        threshold_ns=1, batch_size=3, circuit=dict(array=[3, 2, False], target=[1, 1], cell_type="SRAM_6T_CELL",
        cell_6t=None, operation="read", variation_mode="custom", real_cell_mode=0, w_rc=True, interconnect_config=None)),
        budget_unit="xyce_samples", budget_per_algorithm=4, max_algorithm_draws=4, seeds=[2],
        estimators=[dict(id="mc", algorithm="mc", mode="reference")])


def presets():
    original = read_json(ROOT / "docs/research/hold_workflow_cases/comparison.json")
    comparison = copy.deepcopy(original)
    base = next(p for p in comparison["estimators"] if p["algorithm"] == "mnis")
    base["id"] = "mnis-base"
    base["proposal"]["target_fraction"] = 0
    mixed = copy.deepcopy(base)
    mixed["id"] = "mnis-mixed-50"
    mixed["proposal"]["target_fraction"] = 0.5
    comparison.update(estimators=[base, mixed], seeds=[9101, 9102, 9103],
                      budget_per_algorithm=12, max_algorithm_draws=12)
    for profile in comparison["estimators"]:
        profile["initialization"]["max_simulations"] = 10
    comparison["experiment"]["batch_size"] = 4
    small = copy.deepcopy(comparison)
    small.update(estimators=[dict(id="mc", algorithm="mc", mode="reference")],
                 seeds=[9101], budget_per_algorithm=4, max_algorithm_draws=4)
    return original, [dict(id="comparison", name="两种 MNIS × 三个种子 · 最多 72 次仿真", config=comparison),
                      dict(id="smoke", name="MC 保持 · 单种子 4 样本检查", config=small),
                      dict(id="suite", name="双电路套件 · 保持对照 + 读取 MC", suite=[comparison, read_config()]),
                      dict(id="upstream", name="林同学完整模板 · 最多 1920 次仿真", config=original)]


class LocalJobs:
    def __init__(self, output_root, demo_root=None):
        self.demo = demo_root is not None
        self.demo_root = Path(demo_root).resolve() if self.demo else None
        self.output_root = Path(output_root).resolve() if output_root else self.demo_root
        if not self.demo:
            self.output_root.mkdir(parents=True, exist_ok=True)
        self.allowed_roots = [self.demo_root] if self.demo else [(ROOT / "outputs").resolve(), self.output_root]
        self.jobs = {}
        self.processes = {}
        self.threads = {}
        self.lock = threading.RLock()
        self.closing = False
        from sample_verification import SampleChecks
        self.sample_checks = None
        source = self.demo_root if self.demo else self.output_root
        for path in sorted(source.glob("*/job.json")):
            job = read_json(path)
            job["directory"] = str(path.parent)
            if not self.demo and job["status"] in ("running", "cancelling"):
                job.update(status="interrupted", error="服务重启，未自动接管或重跑实验；请核对原计算进程和已写入文件。")
                if job.get("kind") == "suite":
                    for item in job["suite"]:
                        if item.get("status") == "running":
                            item["status"] = "interrupted"
                        elif item.get("status") == "not_started":
                            item["status"] = "not_started_after_interrupt"
                    self._suite_status(job)
                self._save(job)
            analysis_path = path.parent / "analysis/analysis.json"
            if analysis_path.is_file():
                job["analysis"] = read_json(analysis_path)
            self.jobs[job["id"]] = job

    def verification_source(self):
        return source_snapshot()

    def checks(self):
        with self.lock:
            if self.sample_checks is None:
                from sample_verification import SampleChecks
                self.sample_checks = SampleChecks(self, ROOT)
            return self.sample_checks

    def _save(self, job):
        write_json(Path(job["directory"]) / "job.json", {k: v for k, v in job.items() if k != "analysis"})

    def catalog(self):
        if self.demo:
            return []
        paths = sorted((ROOT / "outputs").glob("bench_web_*/analysis_manifest.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        paths += sorted((ROOT / "outputs/local_yield").glob("*/execution/analysis_manifest.json"))
        entries = []
        for path in paths:
            config_path = path.with_name("experiment.json")
            if not config_path.is_file():
                config_path = path.parent.parent / "request.json"
            label = path.parent.name
            if config_path.is_file():
                config = read_json(config_path)
                e = config.get("experiment", {})
                names = ", ".join(p.get("id", "?") for p in config.get("estimators", []))
                label = f"{e.get('case', label)} · {names} · {len(config.get('seeds', []))} 粒种子"
            entries.append(dict(manifest=str(path), label=label, source="local"))
        return entries

    def _check_runnable(self):
        if self.demo:
            raise ValueError("演示模式只读：不能校验新配置或启动仿真。请使用完整模式连接 OpenYield 环境。")
        upstream()
        snapshot = source_snapshot()
        if snapshot["git_revision"] != _backend_revision:
            raise ValueError("服务启动后源码版本已改变，请重启工作台以避免内存代码与执行代码不一致。")
        if snapshot["git_tracked_files_modified"]:
            raise ValueError("后端有未提交的已跟踪修改，请先提交以冻结本轮源码。")
        return snapshot

    def _references(self, references, seeds):
        if not isinstance(references, list) or not all(isinstance(p, str) and p for p in references):
            raise ValueError("独立参考须为目录路径列表。")
        references = [str(contained(p, self.allowed_roots)) for p in references]
        if any(not (Path(p) / "run_summary.json").is_file() for p in references):
            raise ValueError("独立参考目录需要包含 run_summary.json。")
        ref_seeds = [read_json(contained(Path(p) / "run_summary.json", self.allowed_roots)).get("seed") for p in references]
        if any(type(s) is not int for s in ref_seeds) or len(set(ref_seeds)) != len(ref_seeds):
            raise ValueError("各份独立参考必须记录不同的有效种子。")
        if set(ref_seeds).intersection(seeds):
            raise ValueError("参考种子不能与本场实验种子相同。")
        return references

    def start(self, request, offline=False):
        if not isinstance(request, dict):
            raise ValueError("请求须为 JSON 对象。")
        name = request.get("name", "已有实验分析" if offline else "对照实验")
        if not isinstance(name, str) or len(name) > 120:
            raise ValueError("实验名称须为不超过 120 字的文本。")
        if offline:
            if self.demo:
                raise ValueError("演示模式只读：证据已随包提供，不能重新分析外部清单。")
            manifest = self._validate_manifest(request["manifest"])
            config = self._find_config(manifest)
            references = list(dict.fromkeys(str((manifest.parent / (r if isinstance(r, str) else r["run"])).resolve())
                                            for g in read_json(manifest).get("groups", []) for r in g.get("reference_runs", [])))
            limit = None
        else:
            source = self._check_runnable()
            config = preview(request["config"])["config"]
            references = self._references(request.get("references", []), config["seeds"])
            limit = request.get("time_limit_seconds", 600)
            if type(limit) is not int or not 1 <= limit <= 3600:
                raise ValueError("整场运行限时须为 1–3600 秒。")
            if len(config["estimators"]) * len(config["seeds"]) > 200:
                raise ValueError("本地融合版每场最多 200 轮，请拆分实验计划。")
        with self.lock:
            if self.closing:
                raise ValueError("服务正在关闭。")
            if any(j["status"] in ("running", "cancelling") for j in self.jobs.values()):
                raise ValueError("已有任务运行中，请等待结束；本版暂不提供排队。")
            identifier = uuid.uuid4().hex
            folder = self.output_root / identifier
            folder.mkdir(exist_ok=False)
            known_local = not offline or any(e["manifest"] == str(manifest) for e in self.catalog()) or self.output_root in manifest.parents
            job = dict(id=identifier, name=name.strip() or "未命名实验", status="running", offline=offline,
                       source="local" if known_local else "imported", directory=str(folder), created_at=now(), config=config,
                       references=references, time_limit_seconds=limit,
                       backend_source=source if not offline else None,
                       plan_count=len(config["estimators"]) * len(config["seeds"]) if config else None,
                       manifest=str(manifest) if offline else str(folder / "execution/analysis_manifest.json"))
            self.jobs[identifier] = job
            self._save(job)
            operation = (lambda: self._analyze(job, manifest)) if offline else (lambda: self._execute(job))
            thread = threading.Thread(target=self._work, args=(job, operation), daemon=True)
            self.threads[identifier] = thread
            thread.start()
            return copy.deepcopy(job)

    def start_suite(self, request):
        source = self._check_runnable()
        if not isinstance(request, dict):
            raise ValueError("请求须为 JSON 对象。")
        experiments = request.get("experiments")
        if not isinstance(experiments, list) or not 1 <= len(experiments) <= 8:
            raise ValueError("套件须包含 1–8 个电路实验配置。")
        name = request.get("name", "多电路套件")
        if not isinstance(name, str) or len(name) > 120:
            raise ValueError("套件名称须为不超过 120 字的文本。")
        limit = request.get("time_limit_seconds", 1800)
        if type(limit) is not int or not 1 <= limit <= 3600:
            raise ValueError("套件整场限时须为 1–3600 秒。")
        plans, total = [], 0
        for index, item in enumerate(experiments):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index + 1} 个实验须为对象。")
            result = preview(item["config"])
            references = self._references(item.get("references", []), result["config"]["seeds"])
            total += result["sample_cap"]
            plans.append(dict(index=index, label=circuit_label(result["config"]), config=result["config"],
                              references=references, tasks=result["tasks"], sample_cap=result["sample_cap"]))
        if total > 400:
            raise ValueError("套件仿真上限合计超过 400，请拆分或降低预算。")
        with self.lock:
            if self.closing:
                raise ValueError("服务正在关闭。")
            if any(j["status"] in ("running", "cancelling") for j in self.jobs.values()):
                raise ValueError("已有任务运行中，请等待结束；本版暂不提供排队。")
            identifier = uuid.uuid4().hex
            folder = self.output_root / identifier
            folder.mkdir(exist_ok=False)
            job = dict(id=identifier, name=name.strip() or "多电路套件", status="running", offline=False, kind="suite",
                       source="local", directory=str(folder), created_at=now(), config=None, references=[],
                       time_limit_seconds=limit, plan_count=sum(p["tasks"] for p in plans), suite=plans,
                       backend_source=source, termination_grace_seconds=TERMINATION_GRACE_SECONDS,
                       manifest=str(folder / "suite_manifest.json"))
            from yield_estimation.evaluation.suite_analysis import freeze_suite
            freeze_suite(folder, plans)
            self._suite_status(job)
            self.jobs[identifier] = job
            self._save(job)
            thread = threading.Thread(target=self._work, args=(job, lambda: self._execute_suite(job)), daemon=True)
            self.threads[identifier] = thread
            thread.start()
            return copy.deepcopy(job)

    def _work(self, job, operation):
        try:
            operation()
        except Exception as exc:
            with self.lock:
                job.update(status="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            with self.lock:
                self.processes.pop(job["id"], None)
                if job["status"] == "running":
                    job["status"] = "complete"
                job["finished_at"] = now()
                self._save(job)

    def _analyze(self, job, manifest):
        self._validate_manifest(manifest)
        analyze_results, _ = upstream()
        result = analyze_results.analyze(manifest, Path(job["directory"]) / "analysis")
        with self.lock:
            job["analysis"] = result
            job["plan_count"] = sum(g["checks"]["total_runs"] for g in result["groups"])
            self._save(job)

    def _run_child(self, job, config, references, limit, outdir):
        snapshot = self._check_runnable()
        if job.get("backend_source") and snapshot != job["backend_source"]:
            raise ValueError("实验启动后源码或修改状态发生变化，停止后续电路。")
        folder = Path(job["directory"])
        single = outdir.name == "execution"
        request = folder / ("request.json" if single else outdir.name + ".request.json")
        write_json(request, config)
        command = [sys.executable, "-B", "-u", "-m", "yield_estimation.evaluation.experiment",
                   "--config", str(request), "--output-dir", str(outdir)]
        for reference in references:
            command.extend(["--reference-run", reference])
        write_json(folder / ("launch.json" if single else "launch_" + outdir.name + ".json"),
                   dict(command=command, time_limit_seconds=limit, backend_source=snapshot,
                        termination_grace_seconds=TERMINATION_GRACE_SECONDS))
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", CUDA_VISIBLE_DEVICES="")
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        with (folder / "launcher.log").open("a", encoding="utf-8") as stream:
            stream.write(f"\n=== {outdir.name} ===\n")
            stream.flush()
            with self.lock:
                if job.get("cancel_requested"):
                    return None
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                self.processes[job["id"]] = process
                job["pid"] = process.pid
                self._save(job)
            try:
                code = process.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                self._terminate(process)
                code = 124
        with self.lock:
            self.processes.pop(job["id"], None)
        return code

    def _execute(self, job):
        folder = Path(job["directory"])
        code = self._run_child(job, job["config"], job["references"], job["time_limit_seconds"], folder / "execution")
        with self.lock:
            job["exit_code"] = code
            outcome = "cancelled" if job.get("cancel_requested") else ("complete" if code == 0 else "incomplete" if code is not None else "cancelled")
        manifest = Path(job["manifest"])
        if manifest.is_file():
            self._analyze(job, manifest)
        with self.lock:
            job["status"] = "cancelled" if job.get("cancel_requested") else outcome

    def _suite_status(self, job):
        write_json(Path(job["directory"]) / "suite_status.json",
                   {f"circuit{p['index']}": {k: p[k] for k in
                    ("status", "exit_code", "error", "allowed_seconds") if k in p} for p in job["suite"]})

    def _execute_suite(self, job):
        folder = Path(job["directory"])
        deadline = time.monotonic() + job["time_limit_seconds"]
        job["termination_grace_seconds"] = TERMINATION_GRACE_SECONDS
        halted = None
        for plan in job["suite"]:
            remaining = max(0., deadline - time.monotonic())
            if job.get("cancel_requested"):
                halted = "cancelled"
            elif remaining <= 0:
                halted = "timeout"
            if halted:
                plan["status"] = "not_started_after_" + halted
            else:
                plan.update(status="running", allowed_seconds=remaining)
                self._suite_status(job)
                self._save(job)
                try:
                    code = self._run_child(job, plan["config"], plan["references"], remaining,
                                           folder / f"execution_{plan['index']}")
                    if code == 0 and not Path(plan["manifest"]).is_file():
                        raise ValueError("执行器返回成功但没有实验清单，保留为报错。")
                    plan["exit_code"] = code
                    plan["status"] = ("cancelled" if job.get("cancel_requested") else
                                      "timeout" if code == 124 else "complete" if code == 0 else "error")
                except Exception as exc:
                    plan.update(status="error", error=f"{type(exc).__name__}: {exc}")
                if plan["status"] in ("cancelled", "timeout"):
                    halted = plan["status"]
            self._suite_status(job)
            self._save(job)
        # Always replay the entire frozen plan, including circuits never launched.
        self._analyze(job, Path(job["manifest"]))
        with self.lock:
            job["exit_code"] = [p.get("exit_code") for p in job["suite"]]
            job["status"] = ("cancelled" if job.get("cancel_requested") else "timeout" if halted == "timeout"
                             else "complete" if all(p["status"] == "complete" for p in job["suite"]) else "incomplete")

    def _terminate(self, process):
        # Reuse the backend rule: clean the group even if its leader exited first.
        from yield_estimation.evaluation._process_limits import stop_group
        stop_group(process, TERMINATION_GRACE_SECONDS)

    def cancel(self, identifier):
        with self.lock:
            job = self.jobs[identifier]
            if job["offline"] or job["status"] != "running":
                raise ValueError("仅运行中的仿真实验可以取消。")
            job.update(cancel_requested=True, status="cancelling")
            process = self.processes.get(identifier)
            self._save(job)
        if process:
            threading.Thread(target=self._terminate, args=(process,), daemon=True).start()
        return self.snapshot(identifier)

    def close(self):
        if self.sample_checks is not None:
            self.sample_checks.close()
        with self.lock:
            self.closing = True
            ids = [j["id"] for j in self.jobs.values() if not j["offline"] and j["status"] == "running"]
        for identifier in ids:
            self.cancel(identifier)
        for thread in list(self.threads.values()):
            thread.join(timeout=TERMINATION_GRACE_SECONDS + 5)

    def listing(self):
        with self.lock:
            jobs = copy.deepcopy(list(self.jobs.values()))
        summaries = []
        for job in jobs:
            summary = {k: job.get(k) for k in ("id", "name", "status", "source", "created_at", "plan_count", "error", "offline", "kind")}
            summary["analysis_available"] = bool(job.get("analysis"))
            summary["finished_tasks"] = self._finished(job)
            summaries.append(summary)
        return sorted(summaries, key=lambda j: j["created_at"], reverse=True)

    def _finished(self, job):
        folder = Path(job["directory"])
        trials = sorted(folder.glob("execution*/trials.csv")) or ([folder / "execution/trials.csv"] if (folder / "execution/trials.csv").is_file() else [])
        if trials:
            total = 0
            for path in trials:
                with path.open(encoding="utf-8", newline="") as stream:
                    total += sum(1 for row in csv.DictReader(stream)
                                 if row.get("status", row.get("state")) not in
                                 ("not_started", "running", "not_started_after_experiment_timeout", "not_started_after_interrupt"))
            return total
        if job.get("analysis"):
            return sum(r["status"] != "missing_summary" for g in job["analysis"]["groups"] for r in g["runs"])
        return 0

    def snapshot(self, identifier):
        with self.lock:
            job = copy.deepcopy(self.jobs[identifier])
        job["analysis_available"] = bool(job.get("analysis"))
        job["finished_tasks"] = self._finished(job)
        log = Path(job["directory"]) / "launcher.log"
        job["log"] = self._text(log)[0] if log.is_file() else "尚无本场启动日志。"
        return job

    @staticmethod
    def _text(path):
        with path.open("rb") as stream:
            size = path.stat().st_size
            stream.seek(max(0, size - 120000))
            return stream.read(120000).decode("utf-8", errors="replace"), size > 120000

    def _validate_manifest(self, value):
        manifest = contained(value, self.allowed_roots)
        if not manifest.is_file() or manifest.stat().st_size > 1000000:
            raise ValueError("分析清单不存在或超过 1 MB。")
        data = read_json(manifest)
        if isinstance(data, dict) and data.get("kind") == "openyield_suite_v1":
            from yield_estimation.evaluation.suite_analysis import load_suite
            load_suite(manifest, lambda p: contained(p, self.allowed_roots))
            return manifest
        if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
            raise ValueError("需要包含 groups 的上游分析清单，不是实验配置文件。")
        base = manifest.parent
        for group in data["groups"]:
            if not isinstance(group, dict) or not isinstance(group.get("runs"), list):
                raise ValueError("每个分析组需要 runs 列表。")
            references = group.get("reference_runs", [])
            if not isinstance(references, list):
                raise ValueError("reference_runs 须为列表。")
            for entry in group["runs"] + references:
                name = entry if isinstance(entry, str) else entry["run"]
                directory = contained(base / name, self.allowed_roots)
                contained(directory / "run_summary.json", self.allowed_roots)
        if "execution_plan" in data:
            plan_path = contained(base / data["execution_plan"]["path"], self.allowed_roots)
            plan = read_json(plan_path)
            contained(base / plan["experiment"], self.allowed_roots)
        return manifest

    def _find_config(self, manifest):
        data = read_json(manifest)
        if "execution_plan" in data:
            plan = read_json(contained(manifest.parent / data["execution_plan"]["path"], self.allowed_roots))
            return read_json(contained(manifest.parent / plan["experiment"], self.allowed_roots))
        for path in (manifest.with_name("experiment.json"), manifest.parent.parent / "request.json"):
            path = contained(path, self.allowed_roots)
            if path.is_file():
                config = read_json(path)
                if isinstance(config, dict) and "estimators" in config:
                    return config
        return None

    def _run(self, identifier, group_index, run_index):
        if group_index < 0 or run_index < 0:
            raise ValueError("记录索引不能为负数。")
        job = self.snapshot(identifier)
        row = job["analysis"]["groups"][group_index]["runs"][run_index]
        if job.get("saved_evidence_runs"):
            mapped = job["saved_evidence_runs"].get(f"{group_index}/{run_index}")
            if not mapped:
                raise ValueError("旧材料未登记此轮次。")
            directory = contained(Path(job["directory"]) / mapped, [Path(job["directory"]).resolve()])
            return job, row, directory
        if self.demo:
            mapped = job.get("demo_evidence", {}).get(f"{group_index}/{run_index}")
            if not mapped:
                raise ValueError("演示包未包含该轮证据。")
            directory = contained(Path(job["directory"]) / mapped, self.allowed_roots)
            summary = directory / "run_summary.json"
        else:
            summary = contained(row["source"]["summary"], self.allowed_roots)
            directory = summary.parent
        if summary.is_file() and row["source"].get("summary_sha256"):
            if hashlib.sha256(summary.read_bytes()).hexdigest() != row["source"]["summary_sha256"]:
                raise ValueError("原始摘要已变化，请重新分析，避免详情与表格对应不同版本。")
        return job, row, directory

    def _assets(self, directory):
        candidates = dict(summary=[directory / "run_summary.json"], samples=[directory / "samples.csv"],
                          config=[directory / "run_config.json", directory.parent / "config.json"],
                          report=[directory / "report.md"],
                          log=[directory.parent / "process.log", directory / "process.log"],
                          dataset=[directory / "dataset.csv"])
        paths = {}
        for kind, files in candidates.items():
            for path in files:
                path = contained(path, self.allowed_roots)
                if path.is_file():
                    paths[kind] = path
                    break
        return paths

    def _batches(self, directory):
        if self.demo:
            root = directory / "batches"
            return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        return sorted(p for p in directory.glob("batch_*") if p.is_dir())

    def asset(self, identifier, group_index, run_index, kind):
        _, _, directory = self._run(identifier, group_index, run_index)
        path = self._assets(directory).get(kind)
        if path is None:
            raise ValueError("这轮没有该类型的原始附件。")
        return path

    def batch_file(self, identifier, group_index, run_index, batch, name):
        _, _, directory = self._run(identifier, group_index, run_index)
        batches = {p.name: p for p in self._batches(directory)}
        if batch not in batches:
            raise ValueError("未知仿真批次。")
        if Path(name).name != name or Path(name).suffix not in BATCH_SUFFIXES:
            raise ValueError("仅开放批次内的网表、波形图、扰动与统计文件。")
        path = contained(batches[batch] / name, self.allowed_roots)
        if not path.is_file() or path.stat().st_size > 4000000:
            raise ValueError("该批次文件不存在或超过展示上限。")
        return path

    def dataset(self, identifier, group_index, run_index):
        job, row, directory = self._run(identifier, group_index, run_index)
        bundled = self._assets(directory).get("dataset")
        if bundled:
            return bundled.read_bytes()
        if self.demo:
            raise ValueError("演示包未包含该轮建模数据集。")
        import numpy as np
        samples_path = self._assets(directory).get("samples")
        if not samples_path:
            raise ValueError("该轮没有样本记录，无法导出建模数据集。")
        with samples_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        measure_columns = [k for k in (rows[0].keys() if rows else []) if k not in ("sample", "batch", "xyce_run", "outcome")]
        if not measure_columns:
            raise ValueError("样本记录缺少测量列。")
        by_key = {(r["batch"], r["xyce_run"]): r for r in rows}
        out = io.StringIO()
        writer = None
        for batch in self._batches(directory):
            npy = batch / "samples.npy"
            if not npy.is_file():
                continue
            params = np.load(npy)
            for i in range(params.shape[0]):
                record = by_key.get((batch.name, str(i)))
                if record is None:
                    continue
                if writer is None:
                    header = ["estimator", "seed", "batch", "xyce_run"] + [f"param_{j:03d}" for j in range(params.shape[1])] + measure_columns + ["outcome"]
                    writer = csv.writer(out)
                    writer.writerow(header)
                writer.writerow([row["estimator"], row["seed"], batch.name, i] + [float(v) for v in params[i]]
                                + [record.get(c, "") for c in measure_columns] + [record.get("outcome", "")])
        if writer is None:
            raise ValueError("未找到可配对的参数矩阵与测量记录。")
        return out.getvalue().encode("utf-8")

    def evidence(self, identifier, group_index, run_index, kind, page=1):
        if kind not in ("summary", "samples", "config", "report", "log", "circuit"):
            raise ValueError("未知证据类型。")
        if page < 1:
            raise ValueError("页码从 1 开始。")
        job, row, directory = self._run(identifier, group_index, run_index)
        assets = self._assets(directory)
        if kind == "circuit":
            batches = []
            for batch in self._batches(directory):
                files = sorted(p.name for p in batch.iterdir() if p.is_file() and p.suffix in BATCH_SUFFIXES)
                entry = dict(name=batch.name, files=files)
                for key, suffix in (("waveform", ".png"), ("netlist", ".sp"), ("variation", ".json")):
                    match = next((f for f in files if f.endswith(suffix) and (key != "waveform" or "waveform" in f)), None)
                    if match:
                        entry[key] = f"/api/batch-file?id={identifier}&group={group_index}&run={run_index}&batch={batch.name}&name={match}"
                batches.append(entry)
            note = "批次文件为仿真器原始产物；波形图仅供人工核对节点行为，不代表自动物理验收。"
            if self.demo:
                note += " 演示包为精简子集：每轮仅保留首个批次的电路文件。"
            return dict(kind=kind, available=bool(batches), path=str(directory), assets=list(assets), run=row,
                        truncated=False, content=dict(batches=batches, note=note))
        path = assets.get(kind)
        result = dict(kind=kind, available=bool(path), path=str(path or directory), assets=list(assets),
                      run=row, truncated=False, content=None)
        if kind == "config":
            result["available"] = bool(path or job.get("config") or row.get("planned"))
            result["content"] = dict(experiment=job.get("config"), planned_run=row.get("planned"),
                                     recorded_run=read_json(path) if path else None)
            return result
        if not path:
            result["content"] = "尚未产生该文件，或现有证据未包含该文件；不表示数值为零。"
            return result
        if kind == "samples":
            page_size = 50
            rows, total = [], 0
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                columns = reader.fieldnames or []
                for index, values in enumerate(reader):
                    total += 1
                    if (page - 1) * page_size <= index < page * page_size:
                        rows.append(values)
            result.update(columns=columns, rows=rows, total=total, page=page, page_size=page_size)
        elif kind == "summary":
            result["content"] = read_json(path)
        else:
            result["content"], result["truncated"] = self._text(path)
        return result


def make_handler(jobs, token):
    class Handler(BaseHTTPRequestHandler):
        def send_data(self, value, status=200, content_type="application/json; charset=utf-8", filename=None):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def local_host(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            if not self.local_host():
                self.send_data(dict(error="请使用本机地址打开页面。"), 403)
                return
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == "/api/health":
                    self.send_data(dict(app="openyield-fusion", status="ok", revision=None if jobs.demo else source_snapshot()["git_revision"], demo=jobs.demo))
                elif url.path == "/api/context":
                    if jobs.demo:
                        context = read_json(jobs.demo_root / "context.json")
                        context.update(token=token, jobs=jobs.listing(), catalog=[], demo=True)
                        self.send_data(context)
                    else:
                        original, choices = presets()
                        reference = Path(os.environ.get("OPENYIELD_REFERENCE", ROOT / "outputs/independent-reference"))
                        self.send_data(dict(token=token, template=original, presets=choices, revision=source_snapshot()["git_revision"], backend_source=source_snapshot(), demo=False,
                                            reference=str(reference) if (reference / "run_summary.json").is_file() else "",
                                            output_root=str(jobs.output_root), jobs=jobs.listing(), catalog=jobs.catalog()))
                elif url.path.startswith("/api/sample-check/"):
                    identifier = query["id"][0]
                    checks = jobs.checks()
                    action = url.path.rsplit("/", 1)[1]
                    if action == "availability":
                        self.send_data(checks.availability(identifier, int(query["group"][0]), int(query["run"][0])))
                    elif action == "status":
                        self.send_data(checks.snapshot(identifier, query["check"][0]))
                    elif action == "wave":
                        self.send_data(checks.wave(identifier, query["check"][0], query["sample"][0]))
                    elif action == "download":
                        path = checks.download(identifier, query["check"][0], query["name"][0])
                        mime = "text/csv" if path.suffix == ".csv" else "application/json"
                        self.send_data(path.read_bytes(), content_type=mime+"; charset=utf-8", filename=path.name)
                    else:
                        raise ValueError("未知样本核验操作。")
                elif url.path == "/api/jobs":
                    self.send_data(dict(jobs=jobs.listing()))
                elif url.path == "/api/job":
                    self.send_data(jobs.snapshot(query["id"][0]))
                elif url.path == "/api/download":
                    name = query["name"][0]
                    if name not in TABLES:
                        raise ValueError("请选择上游分析表。")
                    job = jobs.snapshot(query["id"][0])
                    self.send_data(Path(job["directory"], "analysis", name).read_bytes(),
                                   content_type="text/csv; charset=utf-8", filename=name)
                elif url.path == "/api/dataset":
                    args = (query["id"][0], int(query["group"][0]), int(query["run"][0]))
                    self.send_data(jobs.dataset(*args), content_type="text/csv; charset=utf-8", filename="dataset.csv")
                elif url.path == "/api/batch-file":
                    path = jobs.batch_file(query["id"][0], int(query["group"][0]), int(query["run"][0]),
                                           query["batch"][0], query["name"][0])
                    mime = {".png": "image/png", ".sp": "text/plain", ".json": "application/json",
                            ".csv": "text/csv", ".log": "text/plain"}[path.suffix]
                    self.send_data(path.read_bytes(), content_type=mime + "; charset=utf-8")
                elif url.path in ("/api/evidence", "/api/asset"):
                    args = (query["id"][0], int(query["group"][0]), int(query["run"][0]), query["kind"][0])
                    if url.path == "/api/evidence":
                        self.send_data(jobs.evidence(*args, page=int(query.get("page", ["1"])[0])))
                    else:
                        path = jobs.asset(*args)
                        mime = "text/csv" if path.suffix == ".csv" else "text/plain"
                        self.send_data(path.read_bytes(), content_type=mime + "; charset=utf-8", filename=path.name)
                elif url.path in ("/manual", "/MILESTONES.md"):
                    filename = "操作手册.html" if url.path == "/manual" else "MILESTONES.md"
                    mime = "text/html" if url.path == "/manual" else "text/plain"
                    self.send_data((STATIC.parent / filename).read_bytes(), content_type=mime + "; charset=utf-8")
                elif url.path in {"/artifacts/" + name for name in ("overview-desktop.png", "evidence-samples.png", "comparison-six-rounds.png", "builder-guide.png", "circuit-evidence.png", "suite-matrix.png")}:
                    self.send_data((STATIC.parent / "artifacts" / url.path.rsplit("/", 1)[1]).read_bytes(), content_type="image/png")
                elif url.path in ("/", "/app.js", "/style.css", "/sample-verification.js"):
                    name, mime = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                                  "/style.css": ("style.css", "text/css"), "/sample-verification.js": ("sample-verification.js", "text/javascript")}[url.path]
                    self.send_data((STATIC / name).read_bytes(), content_type=mime + "; charset=utf-8")
                else:
                    self.send_data(dict(error="未找到页面或接口。"), 404)
            except (KeyError, IndexError, ValueError, TypeError, OSError) as exc:
                self.send_data(dict(error=str(exc)), 400)

        def do_POST(self):
            allowed = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
            if not self.local_host() or self.headers.get("Origin") not in allowed or self.headers.get("X-OpenYield-Token") != token:
                self.send_data(dict(error="请从本机页面提交请求。"), 403)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 1000000:
                    raise ValueError("配置为空或超过 1 MB。")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError("请求必须是 JSON 对象。")
                if self.path == "/api/sample-check/start":
                    result = jobs.checks().start(request["id"], int(request["group"]), int(request["run"]), request.get("samples"))
                elif self.path == "/api/sample-check/cancel":
                    result = jobs.checks().cancel(request["id"], request["check"])
                elif self.path == "/api/validate":
                    result = preview(request["config"])
                elif self.path == "/api/suite/validate":
                    experiments = request.get("experiments")
                    if not isinstance(experiments, list) or not 1 <= len(experiments) <= 8:
                        raise ValueError("套件须包含 1–8 个电路实验配置。")
                    plans = []
                    for index, item in enumerate(experiments):
                        result = preview(item["config"])
                        plans.append(dict(index=index, label=circuit_label(result["config"]), tasks=result["tasks"],
                                          sample_cap=result["sample_cap"], plan=result["plan"]))
                    result = dict(plans=plans, tasks=sum(p["tasks"] for p in plans), sample_cap=sum(p["sample_cap"] for p in plans))
                elif self.path in ("/api/run", "/api/analyze"):
                    result = jobs.start(request, offline=self.path == "/api/analyze")
                elif self.path == "/api/suite/run":
                    result = jobs.start_suite(request)
                elif self.path == "/api/cancel":
                    result = jobs.cancel(request["id"])
                elif self.path == "/api/shutdown":
                    self.send_data(dict(message="正在保存结果并关闭融合版服务。"))
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                else:
                    self.send_data(dict(error="未知操作。"), 404)
                    return
                self.send_data(result)
            except (KeyError, ValueError, OSError, TypeError) as exc:
                self.send_data(dict(error=str(exc)), 400)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--demo", type=Path, default=None, help="只读演示证据包目录（无需 Xyce）")
    args = parser.parse_args()
    if args.demo:
        jobs = LocalJobs(None, demo_root=args.demo)
    else:
        jobs = LocalJobs(args.output_root or ROOT / "outputs/fusion_workbench")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(jobs, secrets.token_urlsafe(32)))
    mode = "demo" if jobs.demo else "full"
    print(f"OpenYield Fusion ({mode}) http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        jobs.close()
        server.server_close()


if __name__ == "__main__":
    main()
