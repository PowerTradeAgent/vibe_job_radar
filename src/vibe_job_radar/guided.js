"use strict";
const $ = id => document.getElementById(id);
const intake = new URLSearchParams(location.hash.slice(1));
let intakeApplied = false;
const token = intake.get('token') || sessionStorage.getItem('radar-session') || '';
if (token) sessionStorage.setItem('radar-session', token);
if (location.hash) history.replaceState(null,'',location.pathname+location.search);
let requestedTask=new URLSearchParams(location.search).get('task')||'';
let state=null, current=null, selecting=new Set(), loadedId='', requesting=false, sticky='';
const installationNames={not_started:'尚未运行安装',installing:'正在安装',installed:'安装并启动验证成功',installed_not_ready:'安装命令成功，但启动检查失败',dependency_install_failed:'安装失败',dependency_install_timeout:'安装超时'};
const statusNames={queued:'准备中',running:'执行中',ready:'可以选择岗位',waiting_rate:'按来源要求等待',waiting_manual:'需要你处理',paused:'已暂停',completed:'批次结束',stopped:'已停止',interrupted:'上次服务已退出'};
async function api(path,data){const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'X-Radar-Token':token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data),cache:'no-store'});const result=await response.json();if(!response.ok)throw Error(result.error||'操作失败');return result;}
function note(text){$('busy').textContent=text;}
async function act(fn){if(requesting)return;requesting=true;sticky='';try{await fn();await refresh();}catch(e){sticky=e.message;note(sticky);}finally{requesting=false;}}
function options(element,values){const value=element.value;element.replaceChildren();values.forEach(([id,label])=>{const o=document.createElement('option');o.value=id;o.textContent=label;element.append(o);});if(values.some(x=>x[0]===value))element.value=value;}
function active(){if(!current)throw Error('先在第2步创建任务。');return current.id;}
function render(){
 if(!state)return;
 $('environment').textContent=`当前 Python：${state.python}。Playwright：${state.browser_package||'尚未安装'}。安装状态：${installationNames[state.installation]||state.installation}。`;
 const health=state.browser_health;
 if(health){$('browser-summary').textContent=health.message+(health.warnings?.length?'\n'+health.warnings.join('\n'):'');
 $('browser-diagnostic').textContent=JSON.stringify({browser:health,installation:state.setup},null,2);}
 if(!$('site').options.length)options($('site'),state.sites.map(s=>[s.key,s.label+'（实站未验证）']));
 if(!$('role').options.length){options($('role'),Object.entries(state.roles));$('role').value='time_series';}
 if(!intakeApplied){
  intakeApplied=true;
  if(intake.has('role')||intake.has('platform')||intake.has('keyword')){
    const role=intake.get('role'),platform=intake.get('platform'),keyword=intake.get('keyword')||'';
    if(Object.hasOwn(state.roles,role)&&state.sites.some(s=>s.key===platform)&&keyword.trim()&&keyword.length<=100&&!/[\x00-\x1f]/.test(keyword)){
      $('role').value=role;$('site').value=platform;
      $('search-form').elements.keyword.value=keyword;
      sticky='已带入研究目标，尚未访问平台。请核对预算与实际访问范围，再确认开始。';
    }else sticky='目标链接无效，未按该链接创建任务或访问平台。';
  }
}
 $('limits').textContent=`服务端硬限制：页面导航至少 ${state.limits.page_interval} 秒，最多 ${state.limits.pages_hour} 次/小时；经桥接的 HTTP 请求至少 ${state.limits.request_interval} 秒，最多 ${state.limits.requests_hour} 次/小时。不同任务共享配额，不能从界面提高。`;
 options($('task'),state.jobs.map(j=>[j.id,`${j.platform} · ${j.keyword} · ${statusNames[j.status]||j.status}`]));
 if(requestedTask&&state.jobs.some(j=>j.id===requestedTask)){$('task').value=requestedTask;requestedTask='';}
 current=state.jobs.find(j=>j.id===$('task').value)||null;
 if(current&&loadedId!==current.id){selecting=new Set(current.selection||[]);loadedId=current.id;}
 if(current){$('task-status').textContent=`${statusNames[current.status]||current.status}：${current.message}`;
 if(current.status==='waiting_rate'&&current.next_allowed_at){const remaining=Math.max(0,Math.ceil(current.next_allowed_at-Date.now()/1000));$('task-status').textContent+=`\n下次允许时间：${new Date(current.next_allowed_at*1000).toLocaleString()}（约 ${remaining} 秒）。${current.automatic_resume_available?'保留会话，到时自动继续。':'会话已退出或此动作需确认，届时点击继续；不必重填条件。'}`;}
 $('audit').textContent=JSON.stringify(current,null,2);renderCards();
 $('result').replaceChildren(document.createTextNode(`本批已保存 ${current.cards.filter(c=>c.status==='ok').length} 个岗位；发现 ${current.cards.length} 个候选链接。`));
 if(current.report_id){for(const [file,label] of [['requirements_zh.csv','下载岗位要求 CSV'],['descriptions.md','下载描述模板']]){const b=document.createElement('button');b.className='secondary';b.textContent=label;const id=current.report_id;b.onclick=()=>act(()=>downloadReport(id,file));$('result').append(b);}const view=document.createElement('a');view.href='/#report='+current.report_id;view.textContent=' 查看本批研究结论';const a=document.createElement('a');a.href='/advanced#report='+current.report_id;a.textContent=' 用本批要求进入个人证据中心';$('result').append(view,a);}
 }
 for(const button of document.querySelectorAll('button'))button.disabled=state.busy && !['pause','stop'].includes(button.id);
 note(sticky || (state.busy?(!state.active?state.setup?.message:current?.message)||'正在运行后端操作；可以暂停或停止。':''));
}
function renderCards(){const root=$('cards');root.replaceChildren();if(!current.cards.length){root.textContent='还没有岗位清单。完成搜索或人工登录后，点击“读取当前列表”。';return;}
 current.cards.forEach(c=>{const box=document.createElement('div');box.className='card';const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.checked=selecting.has(c.id);input.addEventListener('change',()=>{if(input.checked)selecting.add(c.id);else selecting.delete(c.id);});label.append(input,document.createTextNode(' '+c.title));const source=document.createElement('small');source.textContent='列表观察到的链接：'+c.url;const outcome=document.createElement('small');outcome.textContent='结果：'+c.status+(c.resolved_url?' · 详情真实地址：'+c.resolved_url:'');box.append(label,source,outcome);root.append(box);});}
async function refresh(){state=await api('/api/guided/state');render();}
$('search-form').addEventListener('submit',e=>{e.preventDefault();const f=e.currentTarget;act(async()=>{const data=Object.fromEntries(new FormData(f));data.roles=[data.role];delete data.role;data.max_pages=Number(data.max_pages);data.max_jobs=Number(data.max_jobs);data.consent=f.elements.consent.checked;const r=await api('/api/guided/create',data);loadedId='';await refresh();$('task').value=r.id;render();});});
$('task').addEventListener('change',()=>{loadedId='';render();});
for(const [button,action] of Object.entries({'login':'login','capture':'capture','search-again':'search','pause':'pause','resume':'resume','stop':'stop'}))$(button).addEventListener('click',()=>act(()=>api('/api/guided/action',{id:active(),action})));
$('collect').addEventListener('click',()=>act(()=>api('/api/guided/action',{id:active(),action:'collect',selected:[...selecting]})));
$('select-all').addEventListener('click',()=>{if(!current)return;selecting=new Set(current.cards.slice(0,current.max_jobs).map(c=>c.id));renderCards();});
$('check-browser').addEventListener('click',()=>act(()=>api('/api/guided/check_browser',{})));
$('copy-browser-diagnostic').addEventListener('click',()=>act(async()=>{const text=$('browser-diagnostic').textContent;try{await navigator.clipboard.writeText(text);sticky='诊断已复制；分享前可遮住本机用户名。';}catch{const selection=getSelection();const range=document.createRange();range.selectNodeContents($('browser-diagnostic'));selection.removeAllRanges();selection.addRange(range);sticky='浏览器未允许自动复制；已选中诊断，请按 Ctrl+C。';}}));
$('install').addEventListener('click',()=>{if(confirm('将使用当前Python检查/安装Playwright，并通过Playwright下载配套Chromium，然后实际打开空白浏览器验证。不会访问招聘网站；现有登录会话需先停止。是否继续？'))act(()=>api('/api/guided/install',{consent:true}));});
$('network').addEventListener('click',()=>act(async()=>{const r=await api('/api/guided/diagnose',{platform:$('site').value});$('diagnostic').hidden=false;$('diagnostic').textContent=JSON.stringify(r,null,2)+(r.passed?'\nDNS通过，不代表已登录/允许采集。':'\n先处理代理/DNS；198.18.* 常见于Fake-IP。不要删除公网地址检查。');}));
$('export').addEventListener('click',()=>act(async()=>{const r=await api('/api/guided/export',{id:active()});const url=URL.createObjectURL(new Blob([r.urls.join('\n')],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='observed-job-urls.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
if(!token)note('请从启动终端的完整令牌地址打开基础工作台，再进入本向导。');else refresh().catch(e=>note(e.message));
setInterval(()=>{if(token&&!requesting)refresh().catch(()=>{});},2000);

async function downloadReport(id,file){const r=await fetch(`/api/download/${id}/${file}`,{headers:{'X-Radar-Token':token},cache:'no-store'});if(!r.ok)throw Error('下载失败，请查看报告是否完整。');const u=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=u;a.download=file;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
