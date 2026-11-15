const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { mkdtempSync, readFileSync, writeFileSync } = require('node:fs');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const temp = mkdtempSync(join(tmpdir(), 'offer-native-'));
const bundled = join(process.env.USERPROFILE,'.cache','codex-runtimes','codex-primary-runtime','dependencies','python','python.exe');
const fixture = spawn(bundled, ['-', temp], {windowsHide:true,stdio:['pipe','inherit','inherit']});
fixture.stdin.end(`
from pathlib import Path
import sys
from docx import Document
from docx.shared import Cm, Pt
from docx.oxml.ns import qn
from PIL import Image, ImageDraw
base=Path(sys.argv[1])
portrait=base/'portrait.png'
im=Image.new('RGB',(130,160),'#d7e6ff')
d=ImageDraw.Draw(im)
d.ellipse((46,25,83,62), fill='#5d78ad')
d.rounded_rectangle((25,74,104,143), radius=17,fill='#5d78ad')
im.save(portrait)
doc=Document()
doc.sections[0]._sectPr.find(qn('w:cols')).set(qn('w:num'), '2')
doc.styles['Normal'].font.name='Arial'
doc.styles['Normal'].font.size=Pt(10)
doc.add_heading('演示求职者',0)
doc.add_picture(str(portrait), width=Cm(2.3))
doc.add_heading('教育背景',1)
doc.add_paragraph('统计学本科。使用统计方法进行数据分析，整理数据字典并复核项目结果。')
doc.add_heading('技能',1)
doc.add_paragraph('熟练 SQL、Excel')
doc.add_heading('项目经历',1)
p=doc.add_paragraph(style='List Bullet')
p.add_run('主要负责用 ')
p.add_run('SQL').bold=True
p.add_run(' 做留存分析，完成指标定义和复核。')
doc.add_paragraph('主要参与 Excel 数据看板整理，和团队一起记录问题、复核结果并更新项目说明。',style='List Bullet')
doc.add_paragraph('重复段落不得自动定位。')
doc.add_paragraph('重复段落不得自动定位。')
doc.save(base/'source.docx')
`);
const server = spawn('python',['-B','offer_compass_server.py','--port','18768','--db',join(temp,'test.sqlite3')],{windowsHide:true});
const browser = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
  '--headless','--disable-gpu','--no-first-run','--remote-debugging-pipe','--user-data-dir='+join(temp,'browser'),'about:blank'
],{windowsHide:true,stdio:['ignore','ignore','pipe','pipe','pipe']});
const url='http://127.0.0.1:18768';
const replies=new Map(), errors=[];
let id=0, buffer='', session;
browser.stdio[4].on('data',chunk=>{
  buffer+=chunk;
  let end;
  while((end=buffer.indexOf('\0'))>=0){
    const line=buffer.slice(0,end); buffer=buffer.slice(end+1);
    if(!line) continue;
    const m=JSON.parse(line), waiting=replies.get(m.id);
    if(waiting){ replies.delete(m.id); if(m.error)waiting.reject(Error(JSON.stringify(m.error))); else waiting.resolve(m.result); }
    if(m.method==='Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description);
  }
});
function cdp(method,params={},attached=true){
  return new Promise((resolve,reject)=>{
    replies.set(++id,{resolve,reject});
    browser.stdio[3].write(JSON.stringify({id,method,params,sessionId:attached?session:undefined})+'\0');
  });
}
async function js(expression){
  const reply=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(reply.exceptionDetails) throw Error(reply.exceptionDetails.exception?.description ?? reply.exceptionDetails.text);
  return reply.result.value;
}
async function wait(fn,label){
  for(let i=0;i<300;i++){
    if(await fn())return;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw Error(label);
}
const check=async(expression,label)=>assert.ok(await js(expression),label);
async function page(){
  const {targetId}=await cdp('Target.createTarget',{url:'about:blank'},false);
  ({sessionId:session}=await cdp('Target.attachToTarget',{targetId,flatten:true},false));
  await cdp('Page.enable'); await cdp('Runtime.enable');
}
async function upload(data,name){
  await js(`(() => {
    window.confirm=()=>true;
    const transfer=new DataTransfer();
    transfer.items.add(new File([Uint8Array.from(atob(${JSON.stringify(data)}),c=>c.charCodeAt(0))],${JSON.stringify(name)}));
    $('#resume-file').files=transfer.files;
    $('#resume-file').dispatchEvent(new Event('change'));
  })()`);
  await wait(()=>js("$('#parse-status').textContent.includes('简历已读取')"),'resume upload');
}
async function review(){
  await js("go('j'); $('#jd').value='公司：测试公司\\n岗位：分析师\\n熟练 SQL 和 Excel'; $('#jd').dispatchEvent(new Event('input')); $('#analyze').click(); $('#show-rewrite').click()");
}
async function capture(name){
  const result=await cdp('Page.captureScreenshot',{format:'png'});
  writeFileSync(join(temp,name),Buffer.from(result.data,'base64'));
}
async function run(){
  await new Promise((resolve,reject)=>fixture.on('exit',code=>code===0?resolve():reject(Error('fixture'))));
  await wait(()=>fetch(url+'/api/health').then(r=>r.ok).catch(()=>false),'service');
  const health=await fetch(url+'/api/health').then(r=>r.json());
  assert.equal(health.agent_version,'5.6');
  const agentBody={jd:'岗位要求：\n1. 熟练 SQL 并能够进行数据分析\n2. 具备独立 A/B 测试经验',goal:'判断是否值得投递',
    lines:['项目经历','使用 SQL 分析用户留存，完成指标定义和结果复核。'],levels:{SQL:2}};
  const agentStart=await fetch(url+'/api/agent/run',{method:'POST',headers:{'Content-Type':'application/json','Origin':url},body:JSON.stringify(agentBody)}).then(r=>r.json());
  assert.equal(agentStart.stage,'clarification_required');
  assert.equal(agentStart.tool_trace.length,5);
  const agentSaved=await fetch(url+'/api/agent/run?run_id='+encodeURIComponent(agentStart.run_id)).then(r=>r.json());
  assert.equal(agentSaved.run_id,agentStart.run_id);
  assert.equal(agentSaved.next_action,'wait_for_user');
  await page();
  const {frameTree}=await cdp('Page.getFrameTree');
  const image=readFileSync(join(temp,'portrait.png')).toString('base64');
  await cdp('Page.setDocumentContent',{frameId:frameTree.frame.id,html:`<meta charset="UTF-8"><h1>演示 PDF</h1><img width="90" src="data:image/png;base64,${image}"><h2>技能</h2><p>熟练 SQL</p><h2>项目经历</h2><p>主要负责用 SQL 做留存分析，完成指标定义和复核。</p><p>协同团队整理数据口径，明确统计范围、分析时间和指标定义。对接项目负责人复核分析结果，按时交付文档，复盘整理数据问题和下一阶段的处理计划。</p>`});
  await js('document.images[0].decode()');
  const pdf=(await cdp('Page.printToPDF',{displayHeaderFooter:false})).data;
  await page();
  await cdp('Emulation.setDeviceMetricsOverride',{width:743,height:665,deviceScaleFactor:1,mobile:false});
  await cdp('Page.navigate',{url});
  await wait(()=>js("typeof ready!=='undefined' && ready && $('#resume-file')"),'app ready');
  await js("go('o')");
  await wait(()=>js("Boolean($('#v5-source-form')) && $('#v5-capabilities strong').textContent!=='—'"),'V5 Agent workbench');
  await check("Boolean($('#v5-local-screen')) && Boolean($('#v5-deep-score')) && Boolean($('#v5-model-consent'))",'two-stage job ranking controls');
  await js(`(() => {
    const form=$('#v5-fill-form');
    form.elements.url.value='https://careers.example.com/apply';
    form.elements.fields.value='姓名\\n邮箱\\n登录密码\\n提交申请';
    form.elements.name.value='演示求职者';
    form.elements.email.value='demo@example.com';
    form.requestSubmit();
  })()`);
  await wait(()=>js("$('#v5-fill-result').textContent.includes('最终提交：仅用户本人操作')"),'safe fill plan');
  await check("$('#v5-fill-result').textContent.includes('可填写 2 项') && $('#v5-fill-result').textContent.includes('需人工处理 2 项')",'V5 safe form boundaries');
  await js("go('p')");
  await upload(readFileSync(join(temp,'source.docx')).toString('base64'),'source.docx');
  await wait(()=>js("$('#resume-style').textContent.includes('含嵌入图片') && $('#resume-style').textContent.includes('含分栏')"),'style detection');
  await js("go('j'); $('#jd').value='岗位：数据分析师\\n要求熟练 SQL 并具备数据分析经验\\n具备独立 A/B 测试经验'; $('#jd').dispatchEvent(new Event('input')); $('#analyze').click(); $('#show-preparation').click(); $('#prepare-form').requestSubmit()");
  await wait(()=>js("state.preparation?.question && Boolean($('#clarify-form'))"),'agent asks one clarification');
  await check("document.querySelectorAll('.agent-trace li').length===5 && state.preparation.run_id.startsWith('run_')",'agent trace and persistent run id');
  await js("$('#clarify-form textarea[name=answer]').value='在课程项目中负责实验设计与结果分析。'; $('#clarify-form input[name=confirmed]').checked=true; $('#clarify-form').requestSubmit()");
  await wait(()=>js("state.preparation?.packet?.stage==='ready_for_review'"),'agent creates verified patch');
  await check("state.rewrite?.generatedBy==='agent_v4_3' && state.rewrite.patches.length>0 && state.rewrite.patches.every(item=>item.verified) && document.querySelectorAll('.agent-trace li').length>=12",'only verified structured patches enter review');
  await wait(()=>js("document.querySelectorAll('#agent-history [data-history-run]').length>=2"),'agent run history');
  await js("$('#agent-history [data-history-run]').click()");
  await wait(()=>js("document.querySelectorAll('#agent-history [data-run-detail] ol li').length>0"),'historical run trace detail');
  await review();
  try {
    await wait(()=>js("Boolean($('.word-preview')?.shadowRoot.querySelector('img')) || Boolean($('#original-preview .pdf-page canvas'))"),'Word preview with portrait or native PDF');
  } catch(error){
    console.error(await js("$('#pdf-source-status').textContent"));
    throw error;
  }
  await check("document.querySelectorAll('#review-list button').length===2 && !$('#draft-preview')",'single view with two review items');
  await check("$('#review-list button').click(); !$('#review-change').hidden",'in-place edit card');
  await capture('word-inline.png');
  await check("$('#review-change del').textContent.includes('主要负责用 SQL') && $('#document-mode').textContent.includes('Word 精准修改模式') && $('#review-progress').value === 0",'inline diff, document mode and progress');
  await check("$('#accept-change').click(); $('#review-change').hidden",'accept closes note');
  await wait(()=>js("document.querySelectorAll('#review-list [data-decision=pending]').length===1"),'accepted marker removed');
  await check("($('#review-list [data-decision=pending]')).click(); $('#reject-change').click(); $('#review-change').hidden",'reject in place');
  await wait(()=>js("!$('#download-word').disabled"),'Word export ready');
  await check("$('#review-progress').value === 2 && $('#review-list [data-decision=reject]')",'sidebar tracks decisions');
  await check("Boolean($('.word-preview')?.shadowRoot.querySelector('article')) || Boolean($('#original-preview .pdf-page'))",'revised Word preview uses native PDF or safe browser fallback');
  const content = await js(`(async () => {
    let exported;
    downloadReviewFile=blob=>{exported=blob;};
    $('#download-word').click();
    const bytes=new Uint8Array(await exported.arrayBuffer());
    return btoa(Array.from(bytes,n=>String.fromCharCode(n)).join(''));
  })()`);
  writeFileSync(join(temp,'revised.docx'),Buffer.from(content,'base64'));
  await check("downloadReviewFile=(blob,name)=>{window.primaryWord={blob,name};}; $('#print-resume').click(); primaryWord.name.endsWith('.docx') && primaryWord.blob.type.includes('wordprocessingml')",'primary export follows Word source format');
  await check(`(async () => {
    const file=await originalResume(state.profile.resumeText);
    const draft={edits:['','新的正文'],decisions:{1:'accept'}};
    const duplicate=await revisedWord(file,['','重复段落不得自动定位。'],draft);
    const mixed=await revisedWord(file,['','主要负责用 SQL 做留存分析，完成指标定义和复核。'],draft);
    return duplicate.applied.size===0 && mixed.applied.size===0 && duplicate.skipped.length===1 && mixed.skipped.length===1;
  })()`,'complex and duplicate edits safely skipped');
  await wait(()=>fetch(url+'/api/workspace').then(r=>r.json()).then(saved=>Object.values(saved.data?.rewrite?.decisions??{}).includes('reject')),'database save');
  await cdp('Page.reload');
  await wait(()=>js("typeof ready!=='undefined' && ready && $('#show-rewrite')"),'reload');
  await check("Object.values(state.rewrite.decisions).includes('accept') && Object.values(state.rewrite.decisions).includes('reject')",'review decisions restored');
  await js("go('p')");
  await upload(pdf,'source.pdf');
  await review();
  await wait(()=>js("Boolean($('#original-preview canvas')) && Boolean($('.paper-hit:not([hidden])'))"),'original PDF canvas and hotspots');
  await check("$('.paper-hit:not([hidden])').click(); $('#review-change').parentElement===$('#review-viewer') && !$('#review-change').hidden",'PDF note opens on page');
  await capture('pdf-inline.png');
  await check("$('#accept-all').click(); Boolean($('.paper-hit[data-decision=accept]')) && !$('#download-changes').disabled && $('#download-word').hidden",'bulk PDF approval marks decision and enables checklist');
  await check("$('#review-list button').click(); $('#undo-review').click(); $('#print-resume').disabled && $('#review-progress').value === 0",'sidebar opens resolved change and reopens it');
  await check("$('#accept-all').click(); !$('#print-resume').disabled",'reconfirmed export');
  await check("downloadReviewFile=(blob,name)=>{window.exportedList={blob,name};}; $('#download-changes').click(); exportedList.blob.text().then(text=>text.includes('采纳：负责用 SQL') && exportedList.name.endsWith('.txt'))",'review checklist');
  await check("$('#exit-review').click(); $('#rewrite-editor').hidden && !$('#resume-suggestions').hidden",'return to analysis');
  assert.deepEqual(errors,[]);
  console.log('QA ' + temp);
  console.log('PASS Word and PDF native preview, inline review, file export, and database restoration');
}
const watchdog=setTimeout(()=>{browser.kill();server.kill();fixture.kill();process.exit(2);},120000);
run().catch(error=>{console.error(error,errors,'QA '+temp);process.exitCode=1;})
  .finally(()=>{clearTimeout(watchdog);browser.kill();server.kill();fixture.kill();});
