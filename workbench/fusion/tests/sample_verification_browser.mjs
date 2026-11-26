import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(path.join(process.env.FUSION_BROWSER_DEPS,'package.json'));
const {chromium}=require('playwright');
const base=process.env.OPENYIELD_TEST_URL||'http://127.0.0.1:8768',out=process.env.SAMPLE_WEB_ARTIFACTS;
await fs.mkdir(out,{recursive:true});
const ids=JSON.parse(await fs.readFile(path.join(out,'identities.json'),'utf8'));
const browser=await chromium.launch({headless:true,...(process.env.OPENYIELD_BROWSER_EXECUTABLE?{executablePath:process.env.OPENYIELD_BROWSER_EXECUTABLE}:{})});
const page=await browser.newPage({viewport:{width:1440,height:1080},acceptDownloads:true});
const errors=[],requests=[],receipts=[];
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push({url:r.url(),method:r.method()}));
const dialog=page.locator('#detail-dialog'),content=page.locator('#detail-content');
async function openTask(key){
  if(await dialog.isVisible())await page.locator('#close-detail').click();
  await page.locator('.sidebar [data-view="evidence"]').click();
  await page.locator('#scope').selectOption(ids[key]);
  await page.locator('#evidence-content').getByRole('button',{name:'查看证据',exact:true}).first().click();
  await page.locator('#tab-verification').click();
  await content.getByRole('button',{name:'开始离线核验',exact:true}).waitFor();
}
async function start(key){
  await openTask(key);
  const response=page.waitForResponse(r=>r.url().endsWith('/api/sample-check/start')&&r.request().method()==='POST');
  await content.getByRole('button',{name:'开始离线核验',exact:true}).click();
  const res=await response;const c=await res.json();assert.equal(res.status(),200,JSON.stringify(c));
  return c;
}
async function finished(c,state='完成'){
  await content.locator('p.hint').filter({hasText:`本次${state}；`}).waitFor({timeout:30000});
  const r=await (await page.request.get(`${base}/api/sample-check/status?id=${c.job}&check=${c.id}`)).json();
  receipts.push(r);return r;
}
try{
  await page.goto(base);await page.waitForFunction(()=>document.querySelectorAll('#scope option').length>1);
  for(const key of ['control_4','suspected_38','five','unsupported','missing','condition']){
    const c=await start(key),r=await finished(c);
    assert.equal(r.status,'complete');assert.equal(r.new_solver_calls,0);assert.equal(r.evidence_unchanged,true);
    const expected=key==='unsupported'?'不支持':key==='missing'?'证据不足':key==='condition'?'存在冲突':'支持原判断';
    assert.ok(r.results.some(x=>x.comparison===expected));
    if(key==='five'){
      assert.equal(r.results.length,5);
      assert.ok(r.results.some(x=>x.timing_observation==='boundary_unresolved'));
      await content.getByLabel('核验结果筛选').selectOption('证据不足');
      assert.equal(await content.locator('.sample-check-table tbody tr').count(),1);
      await content.getByLabel('核验结果筛选').selectOption('支持原判断');
      assert.equal(await content.locator('.sample-check-table tbody tr').count(),4);
      await content.getByLabel('核验结果筛选').selectOption('all');
    }
    const sample=key==='five'?'fusion-control-3':r.results[0].id;
    const before=requests.filter(x=>x.url.includes('/api/sample-check/wave')).length;
    const row=content.locator('.sample-check-table tbody tr').filter({has:page.locator(`td:first-child:text-is("${sample}")`)});
    await row.getByRole('button',{name:'打开样本',exact:true}).click();
    if(['control_4','suspected_38','five','condition'].includes(key)){
      await content.locator('.sample-check-wave svg').waitFor();
      assert.equal(requests.filter(x=>x.url.includes('/api/sample-check/wave')).length,before+1);
      if(key==='five')await content.locator('.sample-check-wave').getByText(/波形段 3\/3/).waitFor();
      if(key==='control_4'){
        const d=page.waitForEvent('download');await content.getByRole('button',{name:'导出波形图片',exact:true}).click();
        await (await d).saveAs(path.join(out,'sample-4-wave.png'));
      }
    }else await content.locator('.sample-check-wave').getByText(/波形读取或绘图失败/).waitFor();
    await page.screenshot({path:path.join(out,key+'.png'),fullPage:true});
    for(const [label,name] of [['下载逐样本CSV','samples.csv'],['下载详细JSON','check.json']]){
      const pending=page.waitForEvent('download');await content.getByRole('link',{name:label,exact:true}).click();
      const download=await pending;await download.saveAs(path.join(out,key+'-'+name));
      const direct=await page.request.get(`${base}/api/sample-check/download?id=${c.job}&check=${c.id}&name=${name}`);
      assert.deepEqual(await fs.readFile(path.join(out,key+'-'+name)),await direct.body());
    }
    console.log(`Browser clicked ${key}: ${r.results.map(x=>x.id+':'+x.comparison+'/'+x.timing_observation).join(', ')}; detail, waveform or explicit failure, downloads verified.`);
  }
  const c=await start('cancel');
  await content.getByRole('button',{name:'取消本次核验',exact:true}).click();
  const r=await finished(c,'已取消');assert.equal(r.status,'cancelled');assert.ok(r.results.length<r.selected.length);
  assert.ok(r.items.some(x=>x.status==='not_started_after_cancelled'));
  await page.screenshot({path:path.join(out,'cancel.png'),fullPage:true});
  // The selection UI uses recorded IDs, no free path or threshold input.
  await openTask('five');await content.getByLabel('核验对象').selectOption('selected');
  await content.getByLabel('样本编号').fill('fusion-control-3');
  const p=page.waitForResponse(r=>r.url().endsWith('/api/sample-check/start')&&r.request().method()==='POST');
  await content.getByRole('button',{name:'开始离线核验',exact:true}).click();
  const one=await (await p).json();const single=await finished(one);assert.deepEqual(single.selected,['fusion-control-3']);
  assert.equal(single.results.length,1);
  assert.equal(requests.filter(x=>/\/api\/(run|suite\/run)$/.test(x.url)).length,0);
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(out,'browser_receipt.json'),JSON.stringify({receipts,requests,errors,new_simulations:0},null,2));
  console.log('Actual Edge browser: start/filter/detail/lazy segment plot/PNG export/CSV+JSON download/cancel/specified sample PASS. No simulation submission; no page errors.');
}catch(e){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await fs.writeFile(path.join(out,'browser_failure.json'),JSON.stringify({error:String(e),errors,requests},null,2));throw e;}
finally{await browser.close();}
