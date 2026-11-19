"""Build a read-only demo evidence bundle and a static GitHub-Pages site.

Run inside the OpenYield environment (full mode), e.g.:
  python workbench/fusion/tools/build_demo.py --job <suite-job-id> --destination outputs/new-export
Outputs: demo_data/ (for `server.py --demo`) and demo_site/ (static, no server).
The bundle is a deliberately small subset: per run it keeps the summary, samples,
config, report, log, a precomputed modelling dataset, and the FIRST batch's
circuit files (waveform png, netlist, variation, small csv/log).
"""
import argparse
import base64
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import server

BATCH_KEEP = {".png", ".sp", ".json", ".csv", ".log"}
MIME = {".png": "image/png", ".sp": "text/plain", ".json": "application/json", ".csv": "text/csv", ".log": "text/plain"}


def b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--destination", required=True, type=Path,
                        help="New export directory; existing evidence is never overwritten")
    args = parser.parse_args()
    jobs = server.LocalJobs(args.output_root or server.ROOT / "outputs/fusion_workbench")
    job = jobs.snapshot(args.job)
    if not job.get("analysis"):
        raise SystemExit("该任务尚无分析结果，不能打包演示。")
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    demo_root = destination / "demo_data"
    site = destination / "demo_site"
    for directory in (demo_root, site):
        directory.mkdir()
    job_dir = demo_root / job["id"]
    (job_dir / "analysis").mkdir(parents=True)
    shutil.copy(Path(job["directory"]) / "analysis" / "analysis.json", job_dir / "analysis" / "analysis.json")
    for name in server.TABLES:
        shutil.copy(Path(job["directory"]) / "analysis" / name, job_dir / "analysis" / name)
    mapping, evidence, assets, batch_entries, datasets = {}, {}, {}, {}, {}
    for g, group in enumerate(job["analysis"]["groups"]):
        for r, row in enumerate(group["runs"]):
            key = f"{g}/{r}"
            rel = f"runs/{g}_{r}"
            mapping[key] = rel
            run_dir = job_dir / rel
            (run_dir / "batches").mkdir(parents=True)
            source = Path(row["source"]["summary"]).parent
            for name in ("run_summary.json", "samples.csv", "run_config.json", "report.md"):
                if (source / name).is_file():
                    shutil.copy(source / name, run_dir / name)
            log = source.parent / "process.log"
            if log.is_file():
                shutil.copy(log, run_dir / "process.log")
            datasets[f"{job['id']}|{g}|{r}"] = jobs.dataset(job["id"], g, r).decode("utf-8")
            (run_dir / "dataset.csv").write_text(datasets[f"{job['id']}|{g}|{r}"], encoding="utf-8")
            batches = sorted(p for p in source.glob("batch_*") if p.is_dir())
            if batches:
                first = batches[0]
                target = run_dir / "batches" / first.name
                target.mkdir()
                for path in sorted(first.iterdir()):
                    if path.is_file() and path.suffix in BATCH_KEEP:
                        shutil.copy(path, target / path.name)
                        batch_entries[f"{job['id']}|{g}|{r}|{first.name}|{path.name}"] = dict(
                            base64=b64(path), mime=MIME[path.suffix], filename=path.name)
            for kind in ("summary", "samples", "config", "report", "log", "circuit"):
                pages = 1
                if kind == "samples":
                    data = jobs.evidence(job["id"], g, r, kind, 1)
                    pages = max(1, -(-data["total"] // data["page_size"]))
                for page in range(1, pages + 1):
                    data = jobs.evidence(job["id"], g, r, kind, page)
                    if kind == "circuit":
                        prefix = f"{job['id']}|{g}|{r}|"
                        data["content"]["batches"] = [b for b in data["content"]["batches"]
                                                      if any(k.startswith(prefix + b["name"] + "|") for k in batch_entries)]
                        for batch in data["content"]["batches"]:
                            for field in ("waveform", "netlist", "variation"):
                                url = batch.get(field)
                                if not url:
                                    continue
                                name = url.split("name=")[1]
                                entry = batch_entries.get(f"{job['id']}|{g}|{r}|{batch['name']}|{name}")
                                if entry and field == "waveform":
                                    batch[field] = f"data:{entry['mime']};base64,{entry['base64']}"
                    evidence[f"{job['id']}|{g}|{r}|{kind}|{page}"] = data
            for kind in ("summary", "samples", "config", "report", "log"):
                path = jobs._assets(jobs._run(job["id"], g, r)[2]).get(kind)
                if path:
                    assets[f"{job['id']}|{g}|{r}|{kind}"] = dict(text=path.read_text(encoding="utf-8"), mime=MIME.get(path.suffix, "text/plain"), filename=path.name)
    record = {k: job.get(k) for k in ("id", "name", "status", "source", "created_at", "plan_count", "kind", "references", "time_limit_seconds", "manifest")}
    record.update(offline=True, demo_evidence=mapping, analysis=job["analysis"])
    server.write_json(job_dir / "job.json", record)
    original, choices = server.presets()
    context = dict(template=original, presets=choices, revision=(job.get("backend_source") or {}).get("git_revision"),
                   export_source=server.source_snapshot(), reference="", output_root="",
                   demo_note="演示包为真实实验的精简证据子集：每轮保留首个批次的电路文件；样本、配置、报告、日志与建模数据集为全量。")
    server.write_json(demo_root / "context.json", context)
    demo_jobs = server.LocalJobs(None, demo_root=demo_root)
    payload = dict(context=dict(context, token="demo", jobs=demo_jobs.listing(), catalog=[], demo=True),
                   jobs=demo_jobs.listing(),
                   details={job["id"]: dict(job, log=(job.get("log") or "")[-8000:], analysis_available=True, finished_tasks=demo_jobs._finished(job))},
                   evidence=evidence, assets=assets, batch=batch_entries, dataset=datasets,
                   tables={f"{job['id']}|{name}": (job_dir / "analysis" / name).read_text(encoding="utf-8") for name in server.TABLES})
    (site / "demo-data.js").write_text("window.DEMO_DATA = " + json.dumps(payload, ensure_ascii=False, allow_nan=False) + ";\n", encoding="utf-8")
    for name in ("app.js", "style.css"):
        shutil.copy(HERE / "public" / name, site / name)
    index = (HERE / "public" / "index.html").read_text(encoding="utf-8")
    index = index.replace('<script src="/app.js" defer></script>', '<script src="demo-data.js"></script>\n  <script src="app.js" defer></script>')
    index = index.replace('href="/manual"', 'href="manual.html"').replace('href="/MILESTONES.md"', 'href="milestones.md"')
    (site / "index.html").write_text(index, encoding="utf-8")
    shutil.copy(HERE / "操作手册.html", site / "manual.html")
    shutil.copy(HERE / "MILESTONES.md", site / "milestones.md")
    (site / "artifacts").mkdir()
    for name in ("overview-desktop.png", "evidence-samples.png", "comparison-six-rounds.png", "builder-guide.png", "circuit-evidence.png", "suite-matrix.png"):
        shutil.copy(HERE / "artifacts" / name, site / "artifacts" / name)
    total = sum(p.stat().st_size for p in demo_root.rglob("*") if p.is_file())
    site_total = sum(p.stat().st_size for p in site.rglob("*") if p.is_file())
    print(json.dumps(dict(job=job["id"], runs=len(mapping), demo_bytes=total, site_bytes=site_total), ensure_ascii=False))


if __name__ == "__main__":
    main()
