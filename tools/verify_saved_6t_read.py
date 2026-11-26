"""Verify saved 3x2 6T read-zero evidence only. Never launches a simulator."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import sample_audit as io
from check_saved_read_control import instance_pins

RULE = "saved_6t_read0_3x2_v3_record_anchors"
CORE = "XSRAM_6T_CORE_3X2"
CELL = CORE + ":XSRAM_6T_CELL_1_1"
SUPPORTED = dict(cell_type="SRAM_6T_CELL",rows=3,cols=2,target=[1,1],operation="read",
                 real_cell_mode=0,column_mux=False,expected_bit=0,vdd=1.0)
NUMERICAL_ERRORS = ("DC Operating Point Failed","Time step too small","Xyce timed out",
                    "MPI_Abort","Fatal Error","Netlist error")


def pointer(obj, path):
    for key in path:obj=obj[key]
    return obj


def same_number(a,b):
    return math.isclose(a,b,rel_tol=2e-15,abs_tol=1e-18)


def path_for(root, value):
    path=(root/value).resolve()
    if not path.is_relative_to(root):raise ValueError("Evidence path escapes manifest directory")
    return path



def resolve_threshold(case, root):
    """Read the original recorded condition; the manifest is only a request."""
    evidence=dict(state="unavailable",requested_s=case.get("threshold_s"),
                  original_s=None,effective_s=None,sources=[],issues=[])
    specifications=case.get("threshold_sources",[])
    if not specifications:
        evidence["issues"].append(dict(kind="threshold_source_missing",
            detail="No original threshold record; functional observations may continue, performance is unestablished."))
        return evidence
    values=[]
    for spec in specifications:
        try:
            path=path_for(root,spec["file"])
            if path.name not in ("run_config.json","run_summary.json"):
                raise ValueError("Threshold source must be a saved run_config.json or run_summary.json")
            if spec["field"] not in ("threshold_s","threshold_ns","timing_limit_s"):
                raise ValueError("Unsupported original threshold field")
            expected_unit="ns" if spec["field"]=="threshold_ns" else "s"
            if spec.get("unit")!=expected_unit:
                raise ValueError("Declared unit disagrees with explicit original field")
            if spec.get('sha256') and io.sha(path)!=spec['sha256']:
                raise ValueError('Original threshold file differs from the saved batch fingerprint')
            record=io.load(path)
            original=record[spec["field"]]
            if isinstance(original,bool) or not isinstance(original,(float,int)) or not math.isfinite(original) or original<=0:
                raise ValueError("Original threshold is not a finite positive numeric value")
            value=float(original)*(1e-9 if expected_unit=="ns" else 1)
            recorded=spec.get('recorded_value_s')
            if recorded is not None and (isinstance(recorded,bool) or not isinstance(recorded,(int,float))
                    or not math.isfinite(recorded) or recorded<=0
                    or not math.isclose(value,recorded,rel_tol=1e-12,abs_tol=0)):
                raise ValueError('Original threshold differs from the saved batch requirement')
            # Do not silently ignore contradictory recognized fields in the same saved record.
            for key in ("threshold_s","threshold_ns","timing_limit_s"):
                if key in record:
                    other=record[key]
                    if isinstance(other,bool) or not isinstance(other,(float,int)) or not math.isfinite(other) or other<=0:
                        raise ValueError("Invalid original condition field: "+key)
                    other=float(other)*(1e-9 if key=="threshold_ns" else 1)
                    if not math.isclose(value,other,rel_tol=1e-12,abs_tol=0):
                        raise ValueError("Original record has mutually inconsistent threshold fields")
            original_spec=case.get("original") or {}
            if original_spec.get("kind")=="sample_csv" and path.parent!=path_for(root,original_spec["file"]).parent:
                raise ValueError("Threshold record and original samples.csv are not in the same saved run")
            evidence["sources"].append(dict(file=str(path),sha256=io.sha(path),
                json_pointer=[spec["field"]],unit=expected_unit,raw_value=original,value_s=value,
                origin=spec.get("origin"),recorded_value_source=spec.get("recorded_value_source")))
            values.append(value)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            evidence["issues"].append(dict(kind="verification_condition_conflict" if isinstance(exc,ValueError) else "threshold_source_missing",
                detail="Original threshold source cannot be used: "+str(exc)))
    if any(i["kind"]=="verification_condition_conflict" for i in evidence["issues"]):
        evidence["state"]="conflict";return evidence
    if len(values)!=len(specifications) or not values:return evidence
    evidence["original_s"]=values[0]
    if any(not math.isclose(v,values[0],rel_tol=1e-12,abs_tol=0) for v in values):
        evidence["state"]="conflict"
        evidence["issues"].append(dict(kind="verification_condition_conflict",detail="Saved original threshold records disagree"))
        return evidence
    requested=evidence["requested_s"]
    if requested is not None and (isinstance(requested,bool) or not isinstance(requested,(int,float)) or
            not math.isfinite(requested) or not math.isclose(requested,values[0],rel_tol=1e-12,abs_tol=0)):
        evidence["state"]="conflict"
        evidence["issues"].append(dict(kind="verification_condition_conflict",
            detail="Manifest threshold differs from original recorded condition; this is not a circuit-label error. Re-evaluation is not supported."))
        return evidence
    evidence["state"]="bound";evidence["effective_s"]=values[0]
    return evidence


def original_record(case, root):
    spec=case.get("original")
    if not spec:return dict(label=None,basis="missing_original_judgment")
    path=path_for(root,spec["file"])
    with path.open() as stream:
        rows=list(csv.DictReader(stream))
    if spec["kind"]=="sample_csv":
        matches=[r for r in rows if r["sample"]==str(spec["sample"])]
        if len(matches)!=1:raise ValueError("Original sample ID is missing or ambiguous")
        row=matches[0]
        if row["batch"]!=spec["batch"] or int(row["xyce_run"])!=case["row"]:
            raise ValueError("Original sample ID, batch and row disagree")
        label=row["outcome"]
        metric=io.number(row.get("read_delay_s"))
        basis="saved_production_sample_label"
    elif spec["kind"]=="replay_csv":
        matches=[r for r in rows if r["point"]==str(case["source_point"])]
        if len(matches)!=1:raise ValueError("Replay point is missing or ambiguous")
        row=matches[0];metric=io.number(row.get("native_delay_ns"))
        metric=None if metric is None else metric*1e-9
        if row["read_function"]=="functional_failure":label="read_functional_failure"
        elif row.get("native_performance")=="failure":label="timing_failure"
        else:label="functional_pass"
        basis="previous_saved_replay_report_not_a_production_label"
    else:raise ValueError("Unsupported original record format")
    return dict(label=label,basis=basis,record=row,metric_s=metric,
                source=dict(file=str(path),sha256=io.sha(path),csv_record=rows.index(row)+2))


def verify(case, root, cache):
    out=dict(id=case["id"],row=case.get("row"),rule=RULE,original=None,issues=[],
             comparison="证据不足",conflict_categories=[],circuit_observation="not_established",timing_observation="not_established",
             evidence={},sources={},recorded_provenance=case.get("recorded_provenance",{}),limitations=["Voltage/time conclusions concern saved waveform samples; no numerical convergence or continuous-circuit error bound is proved."])
    def issue(kind,detail):
        out["issues"].append(dict(kind=kind,detail=detail))
        category={"identity_conflict":"input_identity","verification_condition_conflict":"verification_condition",
                  "measurement_conflict":"measurement","circuit_observation_conflict":"circuit_observation"}.get(kind)
        if category:
            if category not in out["conflict_categories"]:out["conflict_categories"].append(category)
            out["comparison"]="存在冲突"
    if case.get('record_identity_issue'):issue('identity_conflict',case['record_identity_issue'])
    if 'recorded_batch_state' in case and case['recorded_batch_state']!='return_prepared':
        issue('evidence_missing','Batch recording is not finalized; original completion must not be inferred')
    threshold=resolve_threshold(case,root)
    out["evidence"]["threshold_binding"]=threshold
    for problem in threshold["issues"]:issue(problem["kind"],problem["detail"])
    try:
        out["original"]=original_record(case,root)
        expected=case.get('expected_original_record')
        if expected is not None and out['original'].get('record')!=expected:
            issue('identity_conflict','Original sample CSV differs from the saved batch judgment')
    except (OSError,ValueError,KeyError) as exc:
        issue("identity_conflict",str(exc));out["comparison"]="存在冲突"
    if case.get("contract")!=SUPPORTED:
        issue("unsupported","Only the explicit 3x2 6T read-zero 1V mode0 contract is implemented")
        out["comparison"]="不支持";return out
    files={}
    for role,relative in case["files"].items():
        try:
            p=path_for(root,relative)
            digest=io.sha(p)
            out["sources"][role]=dict(file=str(p),sha256=digest)
            if case.get("sha256",{}).get(role,digest)!=digest:
                issue("measurement_conflict" if role=="measurement" else "identity_conflict","Hash mismatch: "+role)
            files[role]=p
        except (OSError,ValueError) as exc:issue("evidence_missing",role+": "+str(exc))
    required={"deck","table","matrix","measurement","waveform","solver_log"}
    if not required.issubset(files):return out
    row=case["row"]
    if type(row)!=int or row<0 or case.get("waveform_segment",row)!=row:
        issue("identity_conflict","One row must identify table, matrix, measurement and waveform segment")
        out["comparison"]="存在冲突";return out
    if not files["measurement"].name.endswith(".mt"+str(row)):
        issue("identity_conflict","Measurement suffix does not match zero-based row")
    text=files["deck"].read_text();lines=text.splitlines()
    def locate(fragment):
        return [i for i,line in enumerate(lines,1) if fragment.upper() in line.upper()]
    # An explicit alias is the only allowed resolution of old absolute table paths.
    def resolve_include(value):
        if value in case.get("include_aliases",{}):
            resolved=path_for(root,case["include_aliases"][value])
        elif Path(value).is_absolute():
            raise ValueError("Absolute include has no manifest alias; no private-path fallback")
        else:resolved=(files["deck"].parent/value).resolve()
        if not resolved.is_relative_to(root):raise ValueError("Include escapes evidence root")
        return resolved
    findings=[]
    try:
        mapping=io.check_mapping(files["matrix"].parent,text,row,dict(rows=3,cols=2),
            findings,matrix_path=files["matrix"],table_path=files["table"],resolve_include=resolve_include)
        out["evidence"]["mapping"]=dict(mapping,findings=findings)
        serious=[f for f in findings if f["detail"]!="saved draw differs from actual .data export"]
        if serious:issue("identity_conflict","Input schema, role binding, include or parameter domain mismatch")
        matrix=np.load(files["matrix"],allow_pickle=False)
        table_lines=[line[1:].split() for line in files["table"].read_text().splitlines() if line.startswith("+")]
        exported=np.asarray(table_lines[1:],float)
        if matrix.shape!=exported.shape or row>=len(matrix):raise ValueError("Input row count or shape mismatch")
        if not mapping.get("table_matches"):
            fmt=case.get("recorded_export_format")
            rounded=np.asarray([[float(format(v,fmt)) for v in x] for x in matrix]) if fmt==".4f" else None
            if rounded is not None and np.array_equal(rounded,exported):
                issue("recorded_export_precision_difference","Saved draw differs; table exactly equals recorded .4f export. Actual solver table is authoritative.")
            else:issue("identity_conflict","Draw/table mismatch is not explained by an explicitly supported recorded format")
        expected_runs=len(exported)
        out["evidence"]["input_row"]=dict(zero_based_row=row,table_data_line=row+3,
            parameter_count=matrix.shape[1],actual_values=exported[row].tolist(),
            saved_draw_values=matrix[row].tolist(),mapping_basis="Explicit STEP DATA=table row order and saved batch records; not a rerun proof of waveform identity.")
    except (OSError,ValueError,IndexError) as exc:
        issue("identity_conflict","Input mapping could not be established: "+str(exc))
        out["comparison"]="存在冲突";return out
    delay=re.search(r"(?im)^\.meas\s+TRAN\s+TREAD_TOTAL\s+TRIG\s+V\(WL_EN\)=0\.5\s+RISE=1\s+TARG\s+V\(OUT\)=0\.5\s+FALL=1\s*$",text)
    tran=re.search(r"(?im)^\.TRAN\s+(\S+)\s+(\S+)\s*$",text)
    deadline=re.search(r"(?im)^\.meas\s+TRAN\s+VACCESS_ERROR_0[^\n]*\bAT=(\S+)",text)
    supply=re.search(r"(?im)^V\S*\s+VDD\s+(?:0|VSS)\s+(?:DC\s+)?([0-9.eE+-]+)V?\s*$",text)
    step=re.search(r"(?im)^\.STEP\s+DATA=table\s*$",text)
    if not delay or not tran or not deadline or not supply or not step:
        issue("unsupported","Delay/stimulus/constant supply definition is outside the supported saved protocol")
        out["comparison"]="不支持";return out
    try:tstop=float(tran[2]);when=float(deadline[1]);vdd=float(supply[1])
    except ValueError:
        issue("unsupported","SPICE engineering suffix/expression is outside the explicit numeric protocol")
        out["comparison"]="不支持";return out
    if not same_number(tstop,19e-9) or not same_number(when,7.12e-9) or vdd!=1:
        issue("unsupported","Requires stop 19ns, first-cycle observation 7.12ns, ideal 1V supply")
        out["comparison"]="不支持";return out
    out["evidence"]["protocol"]=dict(vdd=vdd,baseline_s=2.5e-9,observation_s=when,stop_s=tstop,
        delay_definition="First global WL_EN rise through 0.5V to first global OUT fall through 0.5V",
        deck_lines=dict(delay=locate("TREAD_TOTAL"),observation=locate("VACCESS_ERROR_0"),tran=locate(".TRAN")))
    try:
        binding={key:instance_pins(lines,key) for key in ("XSRAM_6T_CORE_3x2","XSRAM_6T_CELL_1_1",
            "XPRECHARGE_1","XSENSEAMP_1","XWRITEDRIVER_1","XD_LATCH")}
        # Establish the cell in this core, not just a similarly named cell elsewhere.
        instance_pins(binding["XSRAM_6T_CORE_3x2"]["body"],"XSRAM_6T_CELL_1_1",definitions=lines)
        for name,expected in ((CELL+":Q",0),(CELL+":QB",1),("OUT",1)):
            pat=r"(?i)V\("+re.escape(name)+r"\)\s*=\s*([0-9.eE+-]+)V?"
            m=re.search(pat,text)
            if not m or float(m[1])!=expected:raise ValueError("Unsupported/unproven initial state: "+name)
        nodes=dict(q=CELL+":Q",qb=CELL+":QB",out="OUT",wl_en="WL_EN",
            wordline=CORE+":"+binding["XSRAM_6T_CELL_1_1"]["pins"]["WL"],
            bl=CORE+":"+binding["XSRAM_6T_CELL_1_1"]["pins"]["BL"],
            blb=CORE+":"+binding["XSRAM_6T_CELL_1_1"]["pins"]["BLB"],
            precharge=binding["XPRECHARGE_1"]["pins"]["ENB"],
            sense=binding["XSENSEAMP_1"]["pins"]["EN"],
            isolation=binding["XSENSEAMP_1"]["pins"]["ISO"],
            write_enable=binding["XWRITEDRIVER_1"]["pins"]["EN"])
        out["evidence"]["node_bindings"]={k:dict(instance=v["instance"],line=v["line"],
             definition=v["definition"],definition_line=v["definition_line"],pins=v["pins"]) for k,v in binding.items()}
    except (ValueError,KeyError) as exc:
        issue("unsupported","Netlist topology/initial state not supported: "+str(exc));out["comparison"]="不支持";return out
    try:
        if files["waveform"] not in cache:
            cache[files["waveform"]]=io.parse_prn(files["waveform"],with_locations=True)
        header,blocks,locations=cache[files["waveform"]]
        if len(blocks)!=expected_runs or row>=len(blocks):
            issue("evidence_missing","Waveform segment count does not match actual parameter table");return out
        data=blocks[row];line_numbers=locations[row];t=data[:,header.index("TIME")]
        if len(t)<2 or np.any(np.diff(t)<=0):raise ValueError("Nonmonotone or empty time segment")
        signals={}
        for name in set(header):
            duplicates=[i for i,key in enumerate(header) if key==name]
            if any(not np.array_equal(data[:,duplicates[0]],data[:,i]) for i in duplicates[1:]):
                issue("identity_conflict","Duplicated waveform columns disagree: "+name)
        for role,node in nodes.items():
            indices=[i for i,k in enumerate(header) if k==("V("+node.upper()+")")]
            if not indices:raise ValueError("Missing waveform node: "+node)
            if any(not np.array_equal(data[:,indices[0]],data[:,i]) for i in indices[1:]):
                issue("identity_conflict","Duplicated node columns disagree: "+node)
            signals[role]=data[:,indices[0]]
        out["evidence"]["waveform"]=dict(segments=len(blocks),selected_segment=row,points=len(t),
            first_time_s=float(t[0]),last_time_s=float(t[-1]),first_data_line=line_numbers[0],last_data_line=line_numbers[-1],nodes=nodes)
    except (ValueError,IndexError) as exc:
        issue("evidence_missing","Waveform cannot support observation: "+str(exc));return out
    log=files["solver_log"].read_text(errors="replace")
    errors=[word for word in NUMERICAL_ERRORS if word.lower() in log.lower()]
    footer="End of Xyce" in files["waveform"].read_text().splitlines()[-1]
    complete=not errors and "End of Xyce" in log and footer and abs(t[0])<1e-18 and t[-1]>=tstop-1e-15
    rc=None
    if case.get("solver_exit"):
        spec=case["solver_exit"]
        try:
            p=path_for(root,spec["file"])
            if spec.get('sha256') and io.sha(p)!=spec['sha256']:
                issue('identity_conflict','Solver attempt record differs from the saved batch fingerprint')
            saved=io.load(p);rc=pointer(saved,spec["pointer"])
            if type(rc) is not int:raise ValueError('Solver native exit is missing or is not an integer')
            if spec.get("point_pointer") and pointer(saved,spec["point_pointer"])!=case["source_point"]:
                issue("identity_conflict","Solver status refers to a different point")
            if spec.get("deck_hash_pointer") and pointer(saved,spec["deck_hash_pointer"])!=io.sha(files["deck"]):
                issue("identity_conflict","Solver exit record deck hash mismatch")
            out["sources"]["solver_exit"]=dict(file=str(p),sha256=io.sha(p),json_pointer=spec["pointer"])
            complete=complete and rc==0
        except (OSError,ValueError,KeyError,IndexError) as exc:
            issue("evidence_missing","Solver exit evidence unavailable: "+str(exc));complete=False
    out["evidence"]["simulation"]=dict(state="completed_by_saved_evidence" if complete else "not_confirmed",
        solver_exit_code=rc,exit_code_recorded=rc is not None,numerical_messages=errors,
        log_end_marker="End of Xyce" in log,waveform_end_marker=footer)
    if not complete:issue("simulation_not_completed","Failure message, incomplete coverage, missing end markers or nonzero exit")
    def at(role,when):
        hi=int(np.searchsorted(t,when,side="left"))
        if hi==0 or hi>=len(t):raise ValueError("Observation not bracketed by saved points")
        lo=hi-1;y=signals[role]
        values=[float(y[lo]),float(y[hi])]
        logic="low" if max(values)<=.2*vdd else "high" if min(values)>=.8*vdd else "indeterminate"
        return dict(value=float(np.interp(when,t,y)),neighbor_values=values,logic=logic,
                    times_s=[float(t[lo]),float(t[hi])],lines=[line_numbers[lo],line_numbers[hi]],node=nodes[role])
    if when>=t[-1] or t[0]>2.5e-9:
        issue("evidence_missing","Baseline/observation not covered");return out
    states={r:at(r,when) for r in ("q","qb","out","bl","blb")}
    baseline={r:at(r,2.5e-9) for r in ("q","qb")}
    active=(t>=2.5e-9)&(t<when)
    wl=active&(signals["wordline"]>=.8*vdd)
    controls=dict(wordline_high_observed=bool(wl.any()),
        sense_high_observed=bool((signals["sense"][active]>=.8*vdd).any()),
        write_disabled=bool((signals["write_enable"][active]<=.2*vdd).all()),
        precharge_off_during_wordline=bool(wl.any() and (signals["precharge"][wl]>=.8*vdd).all()),
        isolation_range=[float(signals["isolation"][active].min()),float(signals["isolation"][active].max())])
    control_sources={}
    for role in ("wordline","precharge","sense","isolation","write_enable"):
        indices=np.flatnonzero(active)
        if not indices.size:raise ValueError("No saved points in control observation window")
        values=signals[role][indices]
        low=int(indices[int(np.argmin(values))]);high=int(indices[int(np.argmax(values))])
        event=io.crossing(t,signals[role],.5,after=2.5e-9)
        if event:
            event["lines"]=[line_numbers[int(np.searchsorted(t,event["lo"]))],line_numbers[int(np.searchsorted(t,event["hi"]))]]
        control_sources[role]=dict(node=nodes[role],window_s=[2.5e-9,when],
            minimum_v=float(signals[role][low]),maximum_v=float(signals[role][high]),
            minimum_line=line_numbers[low],maximum_line=line_numbers[high],first_rise=event)
    wl_rise=control_sources["wordline"]["first_rise"]
    sense_rise=control_sources["sense"]["first_rise"]
    controls["sense_after_wordline"]=bool(wl_rise and sense_rise and wl_rise["value"]<sense_rise["value"]<=when)
    out["evidence"]["baseline"]=baseline;out["evidence"]["observation"]=states;out["evidence"]["controls"]=controls
    out["evidence"]["control_sources"]=control_sources
    controls_ok=all(v for k,v in controls.items() if k!="isolation_range")
    if not controls_ok:issue("evidence_missing","Necessary read controls not established")
    stored_ok=baseline["q"]["logic"]=="low" and baseline["qb"]["logic"]=="high"
    if not stored_ok:issue("evidence_missing","Stored zero before operation not established")
    if stored_ok and controls_ok:
        if states["q"]["logic"]=="high" or states["qb"]["logic"]=="low" or states["out"]["logic"]=="high":
            out["circuit_observation"]="functional_failure"
        elif states["q"]["logic"]=="low" and states["qb"]["logic"]=="high" and states["out"]["logic"]=="low":
            out["circuit_observation"]="functional_pass"
        else:issue("evidence_missing","Read observation is between 20/80 logic bands")
    trigger=io.crossing(t,signals["wl_en"],.5)
    target=io.crossing(t,signals["out"],.5,rising=False)
    for event in (trigger,target):
        if event is not None:
            event["lines"]=[line_numbers[int(np.searchsorted(t,event["lo"]))],line_numbers[int(np.searchsorted(t,event["hi"]))]]
    delay_evidence=dict(trigger=trigger,target=target,definition=RULE,bracket_meaning="Saved crossing endpoints only; not a numerical solver error bound.")
    metric=None
    for n,line in enumerate(files["measurement"].read_text().splitlines(),1):
        m=re.match(r"^\s*TREAD_TOTAL\s*=\s*(\S+)",line)
        if m:
            metric=io.number(m[1]);delay_evidence.update(native_token=m[1],native_s=metric,measurement_line=n)
            break
    prior=out["original"]
    if prior and prior.get("metric_s") is not None and metric is not None and not math.isclose(prior["metric_s"],metric,rel_tol=1e-7,abs_tol=1e-16):
        issue("measurement_conflict","Original CSV metric and native measurement differ")
    if trigger and target and target["value"]>=trigger["value"]:
        lo=target["lo"]-trigger["hi"];hi=target["hi"]-trigger["lo"]
        delay_evidence.update(interpolated_s=target["value"]-trigger["value"],bracket_s=[lo,hi])
        limit=threshold["effective_s"]
        if limit is not None:
            out["timing_observation"]="pass" if hi<=limit else "failure" if lo>limit else "boundary_unresolved"
        if metric is None or not lo-1e-15<=metric<=hi+1e-15:
            issue("measurement_conflict","Native delay absent or inconsistent with saved global events")
    else:
        out["timing_observation"]="event_missing" if target is None or trigger is None else "event_order_invalid"
        if out["circuit_observation"]!="functional_failure":issue("evidence_missing","A missing delay event is not proof of physical failure")
    delay_evidence["threshold_s"]=threshold["effective_s"]
    delay_evidence["requested_threshold_s"]=case.get("threshold_s")
    delay_evidence["event_status"]=out["timing_observation"]
    if threshold["state"]!="bound":
        out["timing_observation"]="condition_conflict" if threshold["state"]=="conflict" else "threshold_unavailable"
    out["evidence"]["delay"]=delay_evidence
    if out["conflict_categories"]:
        out["comparison"]="存在冲突";return out
    if not complete or any(i["kind"] in ("evidence_missing","simulation_not_completed") for i in out["issues"]):return out
    label=prior.get("label") if prior else None
    if label in (None,"unclassified"):
        issue("evidence_missing","No original sample judgment to compare");return out
    obs=out["circuit_observation"];timing=out["timing_observation"]
    if obs=="functional_failure":
        if label in ("read_functional_failure","functional_failure"):out["comparison"]="支持原判断"
        else:issue("circuit_observation_conflict","Saved voltage evidence of functional failure contradicts original label: "+str(label))
    elif obs=="functional_pass":
        if label=="functional_pass":out["comparison"]="支持原判断"
        elif timing in ("pass","failure"):
            desired="pass" if timing=="pass" else "timing_failure"
            if label==desired:out["comparison"]="支持原判断"
            else:issue("circuit_observation_conflict","Function passes, but saved waveform timing under the bound original threshold contradicts label: "+str(label))
        else:issue("evidence_missing","Timing threshold is unresolved or unspecified")
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",required=True,type=Path)
    parser.add_argument("--output-dir",required=True,type=Path)
    args=parser.parse_args();root=args.manifest.resolve().parent;out=args.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=False)
    manifest=io.load(args.manifest)
    if manifest.get("schema_version")!=1:raise ValueError("Use manifest schema_version 1")
    if not manifest.get("cases"):raise ValueError("Manifest contains no cases")
    ids=[c["id"] for c in manifest["cases"]]
    if len(ids)!=len(set(ids)):raise ValueError("Case IDs must be unique")
    results=[];cache={}
    before={str(p):io.sha(p) for p in root.rglob("*") if p.is_file() and not p.is_relative_to(out)}
    for case in manifest["cases"]:
        try:results.append(verify(case,root,cache))
        except (OSError,ValueError,KeyError,IndexError) as exc:
            results.append(dict(id=case.get("id"),comparison="证据不足",conflict_categories=[],circuit_observation="not_established",
                                original=None,rule=RULE,issues=[dict(kind="unreadable_evidence",detail=str(exc))],evidence={}))
    unchanged=all(Path(p).is_file() and io.sha(p)==digest for p,digest in before.items())
    if not unchanged:raise RuntimeError("Input evidence changed")
    summary=dict(rule=RULE,verifier_sha256=io.sha(Path(__file__)),manifest_sha256=io.sha(args.manifest),
                 dependency_hashes={p:io.sha(Path(__file__).parent/p) for p in ("sample_audit.py","check_saved_read_control.py")},results=results,checked=len(results),
                 evidence_unchanged=unchanged,new_solver_calls=0,yield_estimate=None)
    io.dump(out/"evidence.json",summary)
    write_results(out,results)


def write_results(out,results):
    rows=[]
    for r in results:
        states=r.get("evidence",{}).get("observation",{});delay=r.get("evidence",{}).get("delay",{})
        rows.append(dict(id=r["id"],row=r.get("row"),rule=r.get("rule"),original_label=(r.get("original") or {}).get("label"),
            original_basis=(r.get("original") or {}).get("basis"),comparison=r["comparison"],
            conflict_categories=";".join(r.get("conflict_categories",[])),
            threshold_state=r.get("evidence",{}).get("threshold_binding",{}).get("state"),
            simulation_state=r.get("evidence",{}).get("simulation",{}).get("state"),
            recorded_solver_exit=r.get("evidence",{}).get("simulation",{}).get("solver_exit_code"),
            circuit_observation=r.get("circuit_observation"),timing_observation=r.get("timing_observation"),
            q_v=states.get("q",{}).get("value"),qb_v=states.get("qb",{}).get("value"),out_v=states.get("out",{}).get("value"),
            native_delay_s=delay.get("native_s"),threshold_s=delay.get("threshold_s"),delay_bracket_s=json.dumps(delay.get("bracket_s")),
            issues=json.dumps(r["issues"],ensure_ascii=False)))
    with (out/"samples.csv").open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator="\n");writer.writeheader();writer.writerows(rows)
    for row in rows:print(row["id"],row["comparison"],row["circuit_observation"],row["timing_observation"])
    print("Saved CSV and JSON; no plots, no simulator, no yield estimate.")


if __name__=="__main__":
    main()
