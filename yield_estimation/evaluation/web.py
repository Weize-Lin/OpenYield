"""Loopback web interface for the existing experiment and analysis entry points."""
import argparse
import csv
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .analyze_results import analyze
from .experiment import normalize_experiment
from .run import ROOT

STATIC = Path(__file__).with_name("web_static")


def preview(config):
    normalized = normalize_experiment(config)
    tasks = len(normalized["estimators"]) * len(normalized["seeds"])
    return dict(config=normalized, tasks=tasks,
                sample_cap=tasks * normalized["budget_per_algorithm"],
                note="配置通过程序检查。电路可运行性、门限依据和估计精度需要实验验证。")


class LocalJobs:
    def __init__(self, output_root):
        self.output_root = Path(output_root).resolve()
        self.jobs = {}
        self.processes = {}
        self.lock = threading.Lock()

    def start(self, request, *, offline=False):
        if offline:
            manifest = Path(request["manifest"]).expanduser().resolve()
            if not manifest.is_file():
                raise ValueError("找不到分析清单，请填写 analysis_manifest.json 的完整路径。")
            config = None
        else:
            config = preview(request["config"])["config"]
            references = request.get("references", [])
            if not isinstance(references, list) or not all(isinstance(p, str) and p for p in references):
                raise ValueError("独立参考须为摘要目录路径列表。")
            references = [str(Path(p).expanduser().resolve()) for p in references]
            if any(not (Path(p) / "run_summary.json").is_file() for p in references):
                raise ValueError("独立参考目录需要包含 run_summary.json。")
            limit = request.get("time_limit_seconds", 600)
            if type(limit) is not int or not 1 <= limit <= 3600:
                raise ValueError("本次运行限时须为 1–3600 秒。")
        with self.lock:
            if any(job["status"] == "running" for job in self.jobs.values()):
                raise ValueError("已有任务运行中，请等待完成。")
            identifier = uuid.uuid4().hex
            folder = self.output_root / identifier
            folder.mkdir(parents=True, exist_ok=False)
            job = dict(id=identifier, status="running", offline=offline, directory=str(folder))
            self.jobs[identifier] = job
        if offline:
            operation = lambda: self._analyze(job, manifest)
        else:
            operation = lambda: self._execute(job, config, references, limit)
        threading.Thread(target=self._work, args=(job, operation), daemon=True).start()
        return dict(job)

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
                Path(job["directory"], "job.json").write_text(
                    json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _analyze(self, job, manifest):
        result = analyze(manifest, Path(job["directory"]) / "analysis")
        with self.lock:
            job["analysis"] = result

    def _execute(self, job, config, references, limit):
        folder = Path(job["directory"])
        request = folder / "request.json"
        request.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        command = [sys.executable, "-m", "yield_estimation.evaluation.experiment",
                   "--config", str(request), "--output-dir", str(folder / "execution")]
        for reference in references:
            command.extend(["--reference-run", reference])
        (folder / "launch.json").write_text(json.dumps(dict(command=command, time_limit_seconds=limit),
                                                        indent=2) + "\n", encoding="utf-8")
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                   MKL_NUM_THREADS="1", CUDA_VISIBLE_DEVICES="")
        with (folder / "launcher.log").open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            with self.lock:
                self.processes[job["id"]] = process
            try:
                code = process.wait(timeout=limit)
                outcome = "complete" if code == 0 else "incomplete"
            except subprocess.TimeoutExpired:
                self._signal(process, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._signal(process, signal.SIGKILL)
                    process.wait()
                code, outcome = process.returncode, "timeout"
        with self.lock:
            job["exit_code"] = code
        manifest = folder / "execution" / "analysis_manifest.json"
        if manifest.is_file():
            self._analyze(job, manifest)
        with self.lock:
            job["status"] = outcome

    @staticmethod
    def _signal(process, value):
        try:
            os.killpg(process.pid, value)
        except ProcessLookupError:
            pass

    def close(self):
        with self.lock:
            processes = list(self.processes.values())
        for process in processes:
            self._signal(process, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._signal(process, signal.SIGKILL)
                process.wait()

    def snapshot(self, identifier):
        with self.lock:
            job = dict(self.jobs[identifier])
        path = Path(job["directory"])
        log = path / "launcher.log"
        if log.is_file():
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 12000))
                job["log"] = stream.read().decode("utf-8", errors="replace")
        trials = path / "execution" / "trials.csv"
        if trials.is_file():
            with trials.open(encoding="utf-8", newline="") as stream:
                job["finished_tasks"] = len(list(csv.DictReader(stream)))
        return job


def make_handler(jobs, token):
    class Handler(BaseHTTPRequestHandler):
        def send_data(self, value, status=200, content_type="application/json; charset=utf-8"):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get("Host") not in (f"127.0.0.1:{self.server.server_port}",
                                                 f"localhost:{self.server.server_port}"):
                self.send_data(dict(error="请使用本机地址打开页面。"), 403)
                return
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == "/api/context":
                    template = ROOT / "docs/research/hold_workflow_cases/comparison.json"
                    self.send_data(dict(token=token, template=json.loads(template.read_text()),
                                        output_root=str(jobs.output_root)))
                elif url.path == "/api/job":
                    self.send_data(jobs.snapshot(query["id"][0]))
                elif url.path == "/api/download":
                    name = query["name"][0]
                    if name not in ("runs.csv", "methods.csv", "groups.csv", "references.csv"):
                        raise ValueError("请选择结果表。")
                    job = jobs.snapshot(query["id"][0])
                    self.send_data(Path(job["directory"], "analysis", name).read_bytes(),
                                   content_type="text/csv; charset=utf-8")
                elif url.path in ("/", "/app.js", "/style.css"):
                    name, mime = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                                  "/style.css": ("style.css", "text/css")}[url.path]
                    self.send_data((STATIC / name).read_bytes(), content_type=mime + "; charset=utf-8")
                else:
                    self.send_data(dict(error="未找到该页面。"), 404)
            except (KeyError, ValueError, OSError) as exc:
                self.send_data(dict(error=str(exc)), 400)

        def do_POST(self):
            origin = self.headers.get("Origin")
            allowed = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
            if origin not in allowed or self.headers.get("X-OpenYield-Token") != token:
                self.send_data(dict(error="请从本机页面提交请求。"), 403)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 1000000:
                    raise ValueError("配置文件为空或超过 1 MB。")
                request = json.loads(self.rfile.read(length))
                if self.path == "/api/validate":
                    result = preview(request["config"])
                elif self.path in ("/api/run", "/api/analyze"):
                    result = jobs.start(request, offline=self.path == "/api/analyze")
                else:
                    self.send_data(dict(error="未知操作。"), 404)
                    return
                self.send_data(result)
            except (KeyError, ValueError, OSError, TypeError) as exc:
                self.send_data(dict(error=str(exc)), 400)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/local_yield")
    args = parser.parse_args()
    jobs = LocalJobs(args.output_root)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(jobs, secrets.token_urlsafe(32)))
    print(f"OpenYield 本地测评 http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("已关闭本地页面服务。", flush=True)
    finally:
        jobs.close()
        server.server_close()


if __name__ == "__main__":
    main()
