"""Forward-only recording of saved batch evidence. No classification or solver calls."""
import csv
import hashlib
import json
from pathlib import Path
import re


def fingerprint(path):
    if path is None or not path.is_file():return None
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
    return dict(path=str(path.resolve()),sha256=h.hexdigest(),bytes=path.stat().st_size)


def write_batch_evidence(batch, config, options, requirement, first, count, *, state, rows=None, error=None, returned=None):
    batch=Path(batch)
    conditions=batch/"recorded_conditions.json"
    if not conditions.exists():
        conditions.write_text(json.dumps(dict(config=config,circuit=options,requirement=requirement),
                                         indent=2,allow_nan=False)+"\n")
    decks=list(batch.glob("*.sp"));deck=decks[0] if len(decks)==1 else None
    text=deck.read_text() if deck else "";lines=text.splitlines()
    table=None;table_rows=[];mapping_notes=[]
    if deck:
        tables=[]
        for line in lines:
            m=re.match(r"(?i)^\s*\.include\s+(\S+\.data)\s*$",line)
            if m:
                p=Path(m[1]);tables.append(p if p.is_absolute() else deck.parent/p)
        if len(tables)==1:
            table=tables[0]
            if table.exists():
                plus=[(i,l[1:].split()) for i,l in enumerate(table.read_text().splitlines(),1) if l.startswith("+")]
                table_rows=[dict(file_line=i,tokens=t) for i,t in plus[1:]]
        if len(tables)>1:mapping_notes.append("Multiple external data tables; actual row mapping is unresolved")
    if table is None:mapping_notes.append("No explicit external table; simulator-managed sampled device parameters are not recovered by this recorder")
    native_prefix=str(deck) if deck else None
    waveform=Path(native_prefix+".prn") if deck else None
    native_raw_csv=Path(native_prefix+".raw.data.csv") if deck else None
    ledger=Path(native_prefix+".solver_attempts.json") if deck else None
    solver=None
    if ledger and ledger.exists():
        calls=json.loads(ledger.read_text()).get("calls",[])
        if calls:
            call=calls[-1];attempts=call["attempts"];last=attempts[-1] if attempts else {}
            solver=dict(record=fingerprint(ledger),json_pointer=["calls",call["index"]],
                        state=call["state"],native_exit_code=last.get("native_exit_code"),
                        effective_exit_code=last.get("effective_exit_code"))
    parent_config=batch.parent/"run_config.json"
    csv_file=batch.parent/"samples.csv";saved={}
    if csv_file.exists():
        with csv_file.open(newline="") as f:
            reader=csv.DictReader(f)
            for record in reader:
                if record.get("batch")==batch.name:
                    saved[(record.get("sample"),record.get("xyce_run"))]=(reader.line_num,record)
    row_results={int(r[0]):r for r in rows or []}
    samples=[]
    suffix="ms" if options["operation"] in ("hold_snm","read_snm","write_snm") else "mt"
    for i in range(count):
        sample_id=first+i+1;raw=Path(native_prefix+f".{suffix}{i}") if deck else None
        found=saved.get((str(sample_id),str(i)))
        original=row_results.get(sample_id)
        sample=dict(sample=sample_id,batch=batch.name,zero_based_run=i,
                    saved_draw_row=i,actual_table_row=i if table_rows else None,
                    actual_table_line=table_rows[i]["file_line"] if i<len(table_rows) else None,
                    actual_table_row_sha256=hashlib.sha256(" ".join(table_rows[i]["tokens"]).encode()).hexdigest() if i<len(table_rows) else None,
                    native_measurement_expected=str(raw.resolve()) if raw else None,
                    native_measurement=fingerprint(raw),waveform_segment=i,
                    waveform_mapping_basis="Expected STEP or SAMPLING row order; requires independent segment-count/time checks",
                    waveform_segment_independently_confirmed=None,
                    original_judgment=dict(outcome=original[4],measurement_text=original[3],
                        source_file=str(csv_file.resolve()),csv_line=found[0] if found else None,
                        saved_csv_record=found[1] if found else None) if original else None)
        samples.append(sample)
    document=dict(schema_version=1,state=state,submitted_parameter_rows=count,
        algorithm_result_rows_recorded=returned,solver_completed_rows=None,compiler_parsed_rows=None,
        count_definitions=dict(algorithm_result_rows_recorded="The existing recorder's per-batch returned-row count; not completed solver rows"),
        error_type=type(error).__name__ if error else None,error=str(error) if error else None,
        conditions=fingerprint(conditions),run_config=fingerprint(parent_config),requirement=requirement,
        draw_matrix=fingerprint(batch/"samples.npy"),actual_table=fingerprint(table),netlist=fingerprint(deck),
        native_raw_measurements=fingerprint(native_raw_csv),waveform=fingerprint(waveform),
        variation_record=fingerprint(Path(native_prefix+".variation.json")) if deck else None,
        solver=solver,mapping_notes=mapping_notes,samples=samples,
        print_directives=[dict(line=i,text=l) for i,l in enumerate(lines,1) if re.match(r"(?i)^\s*\.PRINT\b",l)],
        time_directives=[dict(line=i,text=l) for i,l in enumerate(lines,1)
                         if re.match(r"(?i)^\s*\.(?:TRAN|OPTIONS\s+OUTPUT)\b",l)],
        measurement_directives=[dict(line=i,text=l) for i,l in enumerate(lines,1)
                                if re.match(r"(?i)^\s*\.meas\b",l)],
        precision_note="Saved directives describe output requests; dense output does not prove independent solver accuracy")
    target=batch/"batch_evidence.json";temporary=batch/"batch_evidence.json.tmp"
    temporary.write_text(json.dumps(document,indent=2,allow_nan=False)+"\n");temporary.replace(target)
    return document
