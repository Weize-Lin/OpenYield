"""Task-scoped, read-only adapter for the existing independent saved-read verifier.

No compiler, classifier or simulator is called here. Files are selected by task
and saved sample identity, never by a path supplied to an HTTP endpoint.
"""
import copy
import csv
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
import threading
import uuid


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def safe(root, value):
    root = Path(root).resolve()
    path = (root / value).resolve()
    if path != root and root not in path.parents:
        raise ValueError("证据文件越过所属记录目录，拒绝读取。")
    return path


def verifier(repository):
    sys.path.insert(0, str(repository / "tools"))
    module = importlib.import_module("verify_saved_6t_read")
    if Path(module.__file__).resolve() != repository / "tools/verify_saved_6t_read.py":
        raise ValueError("核验程序来自其他源码目录，请重启服务。")
    return module


def index_run(directory):
    """Derive only recorded facts. Old adapters must be installed locally, not over HTTP."""
    directory = directory.resolve()
    attached = safe(directory, "saved_read_verification.json")
    if attached.is_file():
        data = load(attached)
        if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
            raise ValueError("旧材料适配清单格式不支持。")
        cases = data["cases"]
        origin = {"kind": "registered_saved_manifest", "file": str(attached), "sha256": sha(attached),
                  "note": data.get("adapter_note", "只登记已保存事实；不补造标签或退出码。")}
    else:
        config_path = safe(directory, "run_config.json")
        config = load(config_path) if config_path.is_file() else {}
        circuit = config.get("circuit", config.get("resolved_circuit", {}))
        array = circuit.get("array", [])
        labels = safe(directory, "samples.csv")
        if not labels.is_file():
            return [], {"kind": "saved_run", "reason": "缺少原逐样本标签 samples.csv，不能建立核验对象。"}
        with labels.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        cases = []
        for record in rows:
            batch = record.get("batch", "")
            if not re.fullmatch(r"batch_[0-9]+", batch):
                raise ValueError("原标签中的批次名无效。")
            folder = safe(directory, batch)
            row = int(record["xyce_run"])
            if row < 0:
                raise ValueError("原样本行号不能为负。")
            decks = sorted(safe(directory,p) for p in folder.glob("*.sp"))
            deck = decks[0] if len(decks) == 1 else folder / "unresolved-deck.sp"
            text = deck.read_text(errors="replace") if deck.is_file() else ""
            zero = re.search(r"(?i)V\(XSRAM_6T_CORE_3X2:XSRAM_6T_CELL_1_1:Q\)\s*=\s*0(?:\.0+)?V?\b", text)
            contract = dict(cell_type=circuit.get("cell_type"), rows=array[0] if len(array)>0 else None,
                cols=array[1] if len(array)>1 else None, column_mux=array[2] if len(array)>2 else None,
                target=circuit.get("target"), operation=circuit.get("operation", config.get("operation")),
                real_cell_mode=circuit.get("real_cell_mode"), expected_bit=0 if zero else None,
                vdd=circuit.get("vdd", config.get("vdd")))
            table = folder / "mc_read_table.data"
            files = dict(deck=deck, matrix=folder/"samples.npy", table=table,
                measurement=Path(str(deck)+".mt"+str(row)), waveform=Path(str(deck)+".prn"),
                solver_log=deck.with_suffix(".log"))
            case = dict(id=str(record["sample"]), row=row, waveform_segment=row, contract=contract,
                original=dict(kind="sample_csv", file="samples.csv", sample=record["sample"], batch=batch),
                files={k:str(p.relative_to(directory)) for k,p in files.items()}, threshold_sources=[],
                include_aliases={}, recorded_provenance=dict(adapter="saved_run_v1", config_file=str(config_path)))
            ledger = safe(directory, folder / "batch_evidence.json")
            batch_record = load(ledger) if ledger.is_file() else {}
            registration = safe(directory.parent, "registration.json")
            registered_source = load(registration).get('source_directory') if registration.is_file() else None
            proven_table_paths = {str(table)}
            if (batch_record.get('actual_table') or {}).get('path'):
                proven_table_paths.add(batch_record['actual_table']['path'])
            if registered_source:
                proven_table_paths.add(str(Path(registered_source)/batch/table.name))
            for name in ("run_config.json", "run_summary.json"):
                path = safe(directory, name)
                if path.is_file():
                    data = load(path)
                    for key in ("threshold_s", "threshold_ns", "timing_limit_s"):
                        if key in data:
                            case["threshold_sources"].append(dict(file=name, field=key, unit="ns" if key=="threshold_ns" else "s"))
            # No basename-only relocation: require the saved ledger or explicit registration source.
            for value in re.findall(r'(?im)^\.include\s+[\"\x27]?([^\s\"\x27]+)', text):
                if value in proven_table_paths:
                    case["include_aliases"][value] = str(table.relative_to(directory))
            if ledger.is_file():
                evidence = batch_record
                case['sha256']={}
                case['files']['batch_record']=str(ledger.relative_to(directory))
                case['sha256']['batch_record']=sha(ledger)
                case['recorded_batch_state']=evidence.get('state')
                for spec in case['threshold_sources']:
                    if spec['file']=='run_config.json' and (evidence.get('run_config') or {}).get('sha256'):
                        spec['sha256']=evidence['run_config']['sha256']
                    if (evidence.get('requirement') or {}).get('threshold_s') is not None:
                        spec['recorded_value_s']=evidence['requirement']['threshold_s']
                        spec['recorded_value_source']=dict(file=str(ledger.relative_to(directory)),json_pointer=['requirement','threshold_s'])
                entries=[s for s in evidence.get('samples',[]) if str(s.get('sample'))==case['id']
                         and s.get('zero_based_run')==row and s.get('batch')==batch]
                if len(entries)==1:
                    entry=entries[0]
                    case['expected_original_record']=(entry.get('original_judgment') or {}).get('saved_csv_record')
                    if (entry.get('native_measurement') or {}).get('sha256'):
                        case['sha256']['measurement']=entry['native_measurement']['sha256']
                    if entry.get('waveform_segment')!=row or entry.get('saved_draw_row')!=row or entry.get('actual_table_row')!=row:
                        case['record_identity_issue']='Batch record row mapping disagrees with the original sample'
                else:
                    case['record_identity_issue']='Batch record has no unique matching sample and row'
                for role,key in (('deck','netlist'),('table','actual_table'),('matrix','draw_matrix'),('waveform','waveform')):
                    if (evidence.get(key) or {}).get('sha256'):
                        case['sha256'][role]=evidence[key]['sha256']
                case["recorded_export_format"] = ".17g" if "17g" in text else None
                saved = (evidence.get("solver") or {}).get("record") or {}
                if saved.get("path"):
                    attempts = safe(directory, batch + "/" + Path(saved["path"]).name)
                    call_pointer = evidence["solver"].get("json_pointer", [])
                    if attempts.is_file():
                        call = load(attempts)
                        for key in call_pointer:
                            call = call[key]
                        attempts_list = call.get("attempts", [])
                        if attempts_list:
                            n = len(attempts_list)-1
                            case["solver_exit"] = dict(file=str(attempts.relative_to(directory)),
                                pointer=call_pointer+["attempts", n, "native_exit_code"],
                                sha256=saved.get("sha256"),
                                deck_hash_pointer=call_pointer+["attempts", n, "deck_sha256"])
            cases.append(case)
        origin = dict(kind="saved_run", config_file=str(config_path), labels_file=str(labels))
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("样本编号重复，不能明确对应。")
    for case in cases:
        # Validate *every* manifest-controlled path before handing it to the verifier.
        paths = list(case.get("files", {}).values()) + list(case.get("include_aliases", {}).values())
        paths += [s["file"] for s in case.get("threshold_sources", [])]
        for name in ("original", "solver_exit"):
            if case.get(name): paths.append(case[name]["file"])
        for value in paths: safe(directory, value)
    return cases, origin


class SampleChecks:
    def __init__(self, jobs, repository):
        self.jobs, self.repository = jobs, repository
        self.lock = threading.RLock()
        self.events, self.threads = {}, {}
        for job in jobs.jobs.values():
            job_root=Path(job["directory"]).resolve()
            for path in safe(job_root,"sample_checks").glob("*/check.json"):
                path=safe(job_root,path)
                data = load(path)
                if data["status"] in ("processing", "cancelling"):
                    data.update(status="interrupted", reason="服务重启；保留部分结果，未自动重跑。")
                    for item in data["items"]:
                        if item["status"] == "processing": item["status"] = "interrupted"
                        elif item["status"] == "not_started": item["status"] = "not_started_after_interrupt"
                    save(path, data)

    def directory(self, job, group, run):
        return self.jobs._run(job, group, run)[2]

    def availability(self, job, group, run):
        directory = self.directory(job, group, run)
        cases, origin = index_run(directory)
        v = verifier(self.repository)
        samples = []
        for case in cases:
            reasons = []
            supported = case.get("contract") == v.SUPPORTED
            if not supported: reasons.append("不支持：仅限3×2 6T、读0、目标(1,1)、1 V、mode0、无列复用及已验证时间协议。")
            for role in ("deck", "table", "matrix", "measurement", "waveform", "solver_log"):
                if role not in case.get("files", {}) or not safe(directory, case["files"][role]).is_file():
                    reasons.append("缺少材料："+role)
            if not case.get("original") or not safe(directory, case["original"]["file"]).is_file():
                reasons.append("缺少原逐样本判断。")
            threshold = v.resolve_threshold(case, directory)
            if threshold["state"] != "bound": reasons.append("原合格要求未绑定或有冲突，性能核验不能成立；功能观察仍可继续。")
            samples.append(dict(id=case["id"], supported=supported, reasons=reasons,
                                ready=supported and not reasons, threshold_state=threshold["state"]))
        return dict(samples=samples, origin=origin, rule=v.RULE, history=self.history(job,group,run),
            note="只检查保存的数据，不启动电路仿真。支持范围及材料缺口按样本显示；有缺口可记录为证据不足，不按电路失败处理。")

    def folder(self, job, check):
        if not re.fullmatch(r"[0-9a-f]{32}", check): raise ValueError("核验编号无效。")
        root=Path(self.jobs.snapshot(job)["directory"]).resolve()
        return safe(root, "sample_checks/"+check)

    def history(self, job, group, run):
        job_root=Path(self.jobs.snapshot(job)["directory"]).resolve()
        root = safe(job_root,"sample_checks")
        result=[]
        for p in sorted(root.glob("*/check.json")):
            data=load(safe(job_root,p))
            if (data["group"], data["run"]) == (group,run):
                result.append({k:data.get(k) for k in ("id","status","created_at","selected","reason")})
        return result

    def start(self, job, group, run, selected=None):
        if self.jobs.demo: raise ValueError("只读演示模式不能创建核验记录，请使用完整模式打开已保存任务。")
        directory = self.directory(job,group,run)
        cases, origin = index_run(directory)
        if selected is not None and (not isinstance(selected,list) or not selected or not all(isinstance(x,str) for x in selected)):
            raise ValueError("指定样本须为非空编号列表。")
        chosen = cases if selected is None else [c for c in cases if c["id"] in selected]
        if not chosen or selected is not None and set(selected)!={c["id"] for c in chosen}:
            raise ValueError("样本不存在或没有原标签，不能创建核验。")
        with self.lock:
            if any(t.is_alive() for t in self.threads.values()): raise ValueError("已有离线核验进行中，请等待或取消。")
            check = uuid.uuid4().hex
            folder = self.folder(job,check);folder.mkdir(parents=True,exist_ok=False)
            v=verifier(self.repository)
            files = [Path(v.__file__), self.repository/"tools/sample_audit.py", self.repository/"tools/check_saved_read_control.py",Path(__file__)]
            data=dict(id=check,job=job,group=group,run=run,status="processing",created_at=datetime.now(timezone.utc).isoformat(),
                selected=[c["id"] for c in chosen],items=[dict(id=c["id"],status="not_started") for c in chosen],
                rule=v.RULE,tool_hashes={str(p.relative_to(self.repository)):sha(p) for p in files},
                source=self.jobs.verification_source(),origin=origin,results=[],new_solver_calls=0,
                input_root=str(directory),evidence_unchanged=None)
            save(folder/"manifest.json",dict(schema_version=1,cases=chosen))
            save(folder/"check.json",data)
            event=threading.Event();self.events[check]=event
            thread=threading.Thread(target=self._work,args=(folder,data,chosen,directory,event),daemon=True)
            self.threads[check]=thread;thread.start()
            return copy.deepcopy(data)

    def _work(self, folder, data, cases, root, event):
        try:
            paths=set()
            for case in cases:
                values=list(case.get("files",{}).values())+[s["file"] for s in case.get("threshold_sources",[])]
                values += [case[k]["file"] for k in ("original","solver_exit") if case.get(k)]
                paths.update(safe(root,p) for p in values)
            before={str(p):sha(p) for p in paths if p.is_file()}
            save(folder/"input_hashes.json",before)
            v=verifier(self.repository);cache={}
            for case,item in zip(cases,data["items"]):
                if event.is_set(): break
                item["status"]="processing";save(folder/"check.json",data)
                try: result=v.verify(case,root,cache)
                except (OSError,ValueError,KeyError,IndexError,TypeError) as exc:
                    result=dict(id=case["id"],comparison="证据不足",original=None,rule=v.RULE,
                        circuit_observation="not_established",timing_observation="not_established",evidence={},sources={},
                        conflict_categories=[],issues=[dict(kind="unreadable_evidence",detail=str(exc))])
                data["results"].append(result);item["status"]="complete"
                v.write_results(folder,data["results"])
                save(folder/"evidence.json",dict(results=data["results"],rule=v.RULE,new_solver_calls=0))
                save(folder/"check.json",data)
            data["evidence_unchanged"]=all(Path(p).is_file() and sha(p)==h for p,h in before.items())
            if not data["evidence_unchanged"]: raise ValueError("核验过程中原证据发生变化，结果不能作为一致记录。")
            data["status"]="cancelled" if event.is_set() else "complete"
            for item in data["items"]:
                if item["status"]=="not_started":item["status"]="not_started_after_cancelled"
        except Exception as exc:
            data.update(status="failed",reason=str(exc))
            for item in data['items']:
                if item['status']=='processing':item['status']='failed'
                elif item['status']=='not_started':item['status']='not_started_after_failure'
        finally:
            save(folder/"check.json",data)

    def snapshot(self, job, check):
        folder=self.folder(job,check)
        return load(safe(folder,"check.json"))

    def cancel(self, job, check):
        with self.lock:
            data=self.snapshot(job,check)
            if data["status"] in ("processing","cancelling"):
                self.events[check].set()
            return dict(id=check,status="cancelling" if data["status"]=="processing" else data["status"],
                        note="当前样本读取结束后停止，已完成与未启动项均保留。")

    def download(self, job, check, name):
        if name not in ("samples.csv","check.json","evidence.json","manifest.json","input_hashes.json"):
            raise ValueError("不支持下载此文件。")
        folder=self.folder(job,check)
        path=safe(folder,name)
        if not path.is_file(): raise ValueError("尚未生成该结果文件；部分完成不等于全部完成。")
        return path

    def wave(self, job, check, sample):
        data=self.snapshot(job,check)
        matches=[r for r in data["results"] if r["id"]==sample]
        if len(matches)!=1:raise ValueError("所选样本尚未完成核验。")
        result=matches[0];e=result.get("evidence",{}).get("waveform")
        if not e:raise ValueError("没有已确认的节点与波形段，无法绘图；请查看核验缺口。")
        root=self.directory(job,data['group'],data['run']).resolve()
        if Path(data['input_root']).resolve()!=root:
            raise ValueError('核验输入目录与所属任务不一致，拒绝读取。')
        source=result["sources"]["waveform"]
        path=safe(root,source["file"])
        if sha(path)!=source["sha256"]:raise ValueError("波形文件已变化，拒绝显示旧核验对应的图。")
        v=verifier(self.repository)
        import numpy as np
        header,blocks=v.io.parse_prn(path)
        segment=e["selected_segment"]
        if len(blocks)!=e["segments"]:raise ValueError("波形段数量变化。")
        block=blocks[segment];t=block[:,header.index("TIME")]
        names=dict(q="存储节点Q",qb="互补存储节点QB",out="读输出OUT",wordline="单元字线",wl_en="字线使能",
            precharge="预充电控制",sense="感放使能",bl="位线BL",blb="互补位线BLB")
        signals={role:block[:,header.index("V("+e["nodes"][role].upper()+")")] for role in names}
        # Display-only extrema-preserving reduction. The verifier above used every saved point.
        indices={0,len(t)-1}
        for a in range(0,len(t),max(1,len(t)//300)):
            b=min(len(t),a+max(1,len(t)//300));indices.add(a);indices.add(b-1)
            for y in signals.values():indices.add(a+int(np.argmin(y[a:b])));indices.add(a+int(np.argmax(y[a:b])))
        ix=sorted(indices)
        return dict(sample=sample,segment=segment,total_segments=len(blocks),original_points=len(t),display_points=len(ix),
            time_unit="ns",voltage_unit="V",time=[float(t[i]*1e9) for i in ix],
            signals=[dict(role=k,label=names[k],node=e["nodes"][k],values=[float(y[i]) for i in ix]) for k,y in signals.items()],
            note="仅画面按区间保留极值；核验使用原始全部保存点。插值与密集打印不证明求解精度。")

    def close(self):
        for event in self.events.values():event.set()
        for thread in self.threads.values():thread.join(5)


def replay_saved_check(check_file, output):
    """Independent CLI replay of the exact web selection, without the HTTP server."""
    check_file=Path(check_file).resolve();folder=check_file.parent;data=load(check_file)
    root=Path(data['input_root']).resolve();output=Path(output).resolve()
    if output==root or root in output.parents:
        raise ValueError('复算输出不能写入原证据目录。')
    v=verifier(Path(__file__).resolve().parents[2])
    for name in ('tools/verify_saved_6t_read.py','tools/sample_audit.py','tools/check_saved_read_control.py'):
        p=Path(__file__).resolve().parents[2]/name
        if data['tool_hashes'].get(name)!=sha(p):raise ValueError('独立核验工具版本已变化，不能冒称相同版本复算。')
    hashes=load(folder/'input_hashes.json')
    if not all(safe(root,p).is_file() and sha(safe(root,p))==h for p,h in hashes.items()):
        raise ValueError('原证据已变化，停止复算。')
    manifest=load(folder/'manifest.json');completed={i['id'] for i in data['items'] if i['status']=='complete'}
    # A cancelled/failed check is replayed only for items it actually completed.
    cases=[c for c in manifest['cases'] if c['id'] in completed]
    output.mkdir(parents=True,exist_ok=False)
    results=[];cache={}
    for case in cases:
        for p in case.get('files',{}).values():safe(root,p)
        results.append(v.verify(case,root,cache))
    save(output/'evidence.json',dict(results=results,rule=v.RULE,new_solver_calls=0,
        source_check=str(check_file),source_status=data['status'],not_replayed=[c['id'] for c in manifest['cases'] if c['id'] not in completed]))
    if results:v.write_results(output,results)
    print('CLI replay finished; only previously completed selections; no simulator.')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='离线复算工作台已保存核验，不启动电路仿真。')
    parser.add_argument('--check',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();replay_saved_check(args.check,args.output_dir)
