"use strict";
window.FusionSampleChecks = (() => {
  const labels = {functional_pass:"读功能成功",functional_failure:"读功能失败",not_established:"尚未确定",
    pass:"满足原要求",failure:"超过原要求",boundary_unresolved:"接近要求，保存点无法确定",event_missing:"缺少延迟事件",
    threshold_unavailable:"原要求来源不足",condition_conflict:"检查条件冲突",event_order_invalid:"事件顺序异常",
    processing:"处理中",complete:"完成",cancelled:"已取消",cancelling:"取消中",failed:"执行失败",interrupted:"服务中断"};
  const categories={input_identity:"参数或样本对应",verification_condition:"检查条件",measurement:"测量",circuit_observation:"电路观察与原标签"};
  const word = x => labels[x] || x || "未知";
  async function mount(container, ctx) {
    const {node,button,api,queryURL,downloadLink} = ctx;
    const args={id:ctx.job,group:ctx.group,run:ctx.run};
    const url=(action,extra={})=>queryURL(`/api/sample-check/${action}`,{...args,...extra});
    container.replaceChildren(node("h4","","样本核验"),node("p","notice","只检查保存的数据，不启动电路仿真。保留原标签，不改变原良率结果或排名。"));
    if(ctx.readOnly){container.append(node("p","notice","只读演示包未开放创建核验；请在完整模式中打开已有任务。"));return;}
    const status=node("p","hint","正在核对支持范围和已有材料…");container.append(status);
    let availability;
    try{availability=await api(url("availability"));}catch(e){status.textContent=`材料检查失败：${e.message}`;return;}
    if(!ctx.current())return;
    status.textContent=availability.note;
    container.append(node("p","hint",`规则：${availability.rule}；材料类型：${availability.origin.kind}。${availability.origin.reason||availability.origin.note||""}`));
    const help=node("details");help.append(node("summary","","查看逐样本支持范围及材料缺口"));
    for(const s of availability.samples)help.append(node("p","",`${s.id}：${s.reasons.join("；")||"基础材料已找到；是否能支持结论由核验决定。"}`));
    container.append(help);
    const controls=node("div","sample-check-controls"),select=node("select"),input=node("input");
    select.setAttribute("aria-label","核验对象");
    for(const [v,t] of [["all","全部样本"],["selected","指定样本"]]){const o=node("option","",t);o.value=v;select.append(o);}
    input.setAttribute("aria-label","样本编号");input.placeholder="样本编号，用逗号分开";input.hidden=true;
    select.onchange=()=>{input.hidden=select.value!=="selected";};
    const output=node("section","sample-check-results"),detail=node("section","sample-check-detail");
    let check=null,filter="all",polling=false;
    const message=node("p","hint","尚未启动核验。"),start=button("开始离线核验",async()=>{
      start.disabled=true;
      try{
        const samples=select.value==="all"?null:input.value.split(/[,，\s]+/).filter(Boolean);
        check=await api("/api/sample-check/start",{...args,samples});await poll();
      }catch(e){message.textContent=`启动失败：${e.message}`;}finally{start.disabled=false;}
    });
    start.id="start-sample-check";start.disabled=!availability.samples.length;
    const cancel=button("取消本次核验",async()=>{try{const r=await api("/api/sample-check/cancel",{id:ctx.job,check:check.id});message.textContent=word(r.status)+"；"+r.note;}catch(e){message.textContent=e.message;}});cancel.hidden=true;
    controls.append(select,input,start,cancel);container.append(controls,message);
    const filters=node("select");filters.setAttribute("aria-label","核验结果筛选");
    for(const v of ["all","支持原判断","存在冲突","证据不足","不支持"]){const o=node("option","",v==="all"?"全部核验结果":v);o.value=v;filters.append(o);}
    filters.onchange=()=>{filter=filters.value;render();};container.append(filters,output,detail);
    const history=node("div","sample-check-controls");container.append(history);
    for(const h of availability.history){history.append(button(`打开核验 ${h.id.slice(0,8)} · ${word(h.status)}`,async()=>{check=await api(url("status",{check:h.id}));render();if(check.status==="processing")await poll();}));}
    function render(){
      if(!check)return;
      message.textContent=`本次${word(check.status)}；已检查 ${check.results.length}/${check.selected.length}。${check.reason||""} 未启动或部分完成不按全部完成解释。`;
      cancel.hidden=!["processing","cancelling"].includes(check.status);
      output.replaceChildren();
      const links=node("div","sample-check-controls");
      links.append(downloadLink("下载逐样本CSV",url("download",{check:check.id,name:"samples.csv"}),"samples.csv"),
        downloadLink("下载详细JSON",url("download",{check:check.id,name:"check.json"}),"check.json"));output.append(links);
      const table=node("table","sample-check-table"),head=node("tr"),body=node("tbody");
      ["样本编号","平台原判断","仿真完成情况","独立读出观察","速度是否满足原要求","核验结论","原因 / 详情"].forEach(x=>head.append(node("th","",x)));
      const thead=node("thead");thead.append(head);table.append(thead,body);
      for(const r of check.results.filter(x=>filter==="all"||x.comparison===filter)){
        const tr=node("tr");tr.dataset.sample=r.id;
        const sim=r.evidence?.simulation;
        const state=sim?.state==="completed_by_saved_evidence"?`保存证据支持完成；退出码 ${sim.solver_exit_code??"未记录"}`:sim?"未确认完成":"未知";
        const reason=[...(r.conflict_categories||[]).map(x=>categories[x]||x),...(r.issues||[]).map(x=>x.detail)].join("；")||"存储、控制、读出及原要求的证据相符。";
        const original = !r.original?.label ? "未记录" : r.original.basis === "previous_saved_replay_report_not_a_production_label"
          ? `历史重放报告：${r.original.label}（非平台原标签）` : r.original.label;
        [r.id,original,state,word(r.circuit_observation),word(r.timing_observation),r.comparison].forEach(x=>tr.append(node("td","",x)));
        const td=node("td");td.append(node("p","",reason),button("打开样本",()=>openSample(r)));tr.append(td);body.append(tr);
      }
      output.append(table);
      for(const item of check.items.filter(i=>i.status!=="complete"))output.append(node("p","hint",`${item.id}：${item.status}（没有核验结论）`));
    }
    async function poll(){
      if(polling)return;polling=true;
      try{do{check=await api(url("status",{check:check.id}));if(!ctx.current())return;render();if(!["processing","cancelling"].includes(check.status))break;await new Promise(r=>setTimeout(r,450));}while(ctx.current());}
      catch(e){message.textContent=`读取核验状态失败：${e.message}。已保存记录不因此消失。`;}
      finally{polling=false;}
    }
    async function openSample(r){
      detail.replaceChildren(node("h4","",`样本 ${r.id} · ${r.comparison}`));detail.dataset.sample=r.id;
      detail.append(node("p","notice","以下是独立检查记录。原标签不被覆盖；保存点的时间范围不等于求解精度保证。"));
      detail.append(node("p","hint",r.original?.basis === "previous_saved_replay_report_not_a_production_label"
        ? "此条原判断来自已保存的重放报告，并非当时平台的逐样本原标签；不能冒充新增的平台标签验证。"
        : `原判断来源：${r.original?.basis||"未记录"}`));
      for(const [title,data] of [["原合格要求及文件/字段来源",r.evidence?.threshold_binding],["关键电压与时间（V、s）",{baseline:r.evidence?.baseline,observation:r.evidence?.observation,delay:r.evidence?.delay}],
        ["输入参数及实际文件位置",{input:r.evidence?.input_row,mapping:r.evidence?.mapping,sources:r.sources}], ["冲突类别、规则与缺口",{rule:r.rule,categories:r.conflict_categories,issues:r.issues}]]){
        const d=node("details");d.append(node("summary","",title),node("pre","",ctx.pretty(data)));detail.append(d);
      }
      const wave=node("div","sample-check-wave");wave.append(node("p","hint","正在读取本样本对应的波形段…"));detail.append(wave);
      try{
        const data=await api(url("wave",{check:check.id,sample:r.id}));
        if(!ctx.current()||detail.dataset.sample!==r.id)return;
        wave.replaceChildren(node("p","hint",`波形段 ${data.segment+1}/${data.total_segments}；原始 ${data.original_points} 点，显示 ${data.display_points} 点。${data.note}`));
        const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");
        svg.setAttribute("viewBox",`0 0 900 ${data.signals.length*125+20}`);svg.setAttribute("role","img");svg.setAttribute("aria-label",`样本${r.id}关键节点波形，时间ns，电压V`);
        function add(tag,attrs,text){const el=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,String(v)));if(text!=null)el.textContent=text;svg.append(el);return el;}
        const min=data.time[0],max=data.time[data.time.length-1];
        data.signals.forEach((signal,i)=>{
          const y=i*125+22,lo=Math.min(-.08,...signal.values),hi=Math.max(1.08,...signal.values),X=t=>75+(t-min)/(max-min)*790,Y=v=>y+80-(v-lo)/(hi-lo)*70;
          add("text",{x:12,y:y+8,"font-size":12,fill:"#172a35"},`${signal.label} · ${signal.node} · V`);
          for(const v of [0,.5,1]){add("line",{x1:75,x2:865,y1:Y(v),y2:Y(v),stroke:"#ddd"});add("text",{x:30,y:Y(v)+4,"font-size":11,fill:"#444"},v);}
          add("polyline",{points:data.time.map((t,j)=>`${X(t)},${Y(signal.values[j])}`).join(" "),fill:"none",stroke:i%2?"#b04b19":"#176f99","stroke-width":1.5});
          for(let j=0;j<=4;j++){const t=min+(max-min)*j/4;add("text",{x:X(t),y:y+98,"font-size":11,fill:"#444"},t.toFixed(2));}
          add("text",{x:810,y:y+115,"font-size":11,fill:"#444"},"时间 (ns)");
        });wave.append(svg);
        wave.append(button("导出波形图片",async()=>{
          const blob=new Blob([new XMLSerializer().serializeToString(svg)],{type:"image/svg+xml;charset=utf-8"}),u=URL.createObjectURL(blob),image=new Image();
          image.onload=()=>{const c=document.createElement("canvas");c.width=1800;c.height=(data.signals.length*125+20)*2;const g=c.getContext("2d");g.fillStyle="white";g.fillRect(0,0,c.width,c.height);g.drawImage(image,0,0,c.width,c.height);c.toBlob(b=>{const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=`sample-${r.id}.png`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);});URL.revokeObjectURL(u);};image.onerror=()=>{wave.append(node("p","notice error","图片导出失败，核验结果和原始波形未改变。"));URL.revokeObjectURL(u);};image.src=u;
        }));
      }catch(e){wave.replaceChildren(node("p","notice error",`波形读取或绘图失败：${e.message}。不能据此解释为没有信号。`));}
    }
  }
  return {mount};
})();
