(() => {
  const root = document.getElementById('dock-preview');
  const initial = [];
  const icons = (name) => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const safe = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let saved;try{saved=JSON.parse(localStorage.getItem('docker-monitor-ui')||'{}');}catch{saved={};}
  const models = new Map();
  const systemAppearance=window.matchMedia('(prefers-color-scheme: dark)');
  const applyTheme=app=>{
    const m=models.get(app),mode=m.theme;
    root.style.colorScheme=mode==='auto'?(systemAppearance.matches?'dark':'light'):mode;
    root.dataset.theme=mode;document.documentElement.style.colorScheme=root.style.colorScheme;
    for(const button of app.querySelectorAll('[data-theme-mode]'))button.setAttribute('aria-pressed',String(button.dataset.themeMode===mode));
  };
  systemAppearance.addEventListener('change',()=>{for(const [app,m] of models)if(m.theme==='auto')applyTheme(app);});
  const memoryLabel = value => value == null ? '—' : value === 0 ? '0 B' : value >= 1024 ? (value/1024).toFixed(2)+' GB' : value+' MB';
  const refreshIcons = () => { for(const el of root.querySelectorAll('[data-tooltip]'))el.title=el.dataset.tooltip; if (globalThis.lucide) lucide.createIcons({root,attrs:{width:16,height:16}}); };
  const badge = c => `<span class="dw-badge ${c.state==='warning'?'warning':c.state==='stopped'?'stopped':''}">${c.state==='warning'?icons('triangle-alert'):'<span class="dw-dot"></span>'}${c.state==='warning'?'健康异常':c.state==='stopped'?'已停止':'运行中'}</span>`;
  const name = (c,inline=false) => `<div class="dw-name-button"><span class="dw-container-icon ${c.self?'dw-program-icon':''}">${c.self?'<img src="/icons/icon.svg?v=a2" alt="Docker Monitor">':icons(c.icon)}</span><span class="dw-name-text"><strong data-tooltip="${safe(c.name)}">${safe(c.name)}</strong><small data-tooltip="${safe(c.image)}">${safe(c.image)}</small>${inline?`<span class="dw-inline-update">${updateStatus(c)}${c.protected?'':`<button type="button" class="dw-auto-button ${c.auto?'enabled':''}" data-policy aria-label="设置 ${safe(c.name)} 自动更新">· 自动${c.auto?'开启':'关闭'}</button>`}</span>`:''}</span></div>`;
  const updateStatus = c => c.busy?`<span class="dw-update-status running-job">${icons('loader-circle')}任务执行中…</span>`:c.update==='error'?`<span class="dw-update-status failed" data-tooltip="${safe(c.updateError||'检查失败')}">${icons('triangle-alert')}检查失败</span>`: c.protected?`<span class="dw-update-status">${icons('shield-check')}${c.self?'本程序 · 已保护':'受保护 · 不参与更新'}</span>`:`<span class="dw-update-status ${c.update==='available'?'available':''}">${icons(c.update==='available'?'circle-arrow-up':c.update==='current'?'check':'clock-3')}${c.update==='available'?'有更新':c.update==='current'?(c.lastResult||'已是最新'):'尚未检查'}</span>`;
  const actions = c => c.protected ? `<span class="dw-protected">${icons('shield-check')}受保护</span>` : `<div class="dw-actions"><button type="button" class="dw-action ${c.update==='available'?'update':''}" data-${c.update==='available'?'action="update"':'check="'+c.id+'"'} data-id="${c.id}">${icons(c.update==='available'?'download':'refresh-cw')}${c.update==='available'?'更新':'检查'}</button>${c.state==='stopped'?`<button type="button" class="dw-action start" data-action="start" data-id="${c.id}">${icons('play')}启动</button>`:`<button type="button" class="dw-action" data-action="restart" data-id="${c.id}">${icons('rotate-cw')}重启</button><button type="button" class="dw-action stop" data-action="stop" data-id="${c.id}">${icons('square')}停止</button>`}</div>`;

  const restore=app=>{
    const state=saved||{};app.dataset.layout='cards';
    return {containers:[],theme:['light','dark','auto'].includes(state.theme)?state.theme:'auto',
      filter:['all','running','warning','stopped'].includes(state.filter)?state.filter:'all',
      search:state.search||'',pending:null,timer:null,lastCheck:'尚未检查',checkCron:'0 */6 * * *',
      updateMode:'scheduled',updateCron:'0 3 * * *',waitValue:24,waitUnit:'hours',history:[],host:null,
      seenJobs:new Set(),trackedJobs:new Set(),design:{radius:18,compact:false}};
  };
  const persist=()=>{
    for(const [app,m] of models)try{localStorage.setItem('docker-monitor-ui',JSON.stringify({view:app.dataset.layout,theme:m.theme,filter:m.filter,search:m.search}));}catch{}
  };
  const api=async(path,options={})=>{
    const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Monitor-Request':'1',...options.headers},signal:AbortSignal.timeout(120000)});
    if(response.status===401){window.location.replace('/login');throw new Error('登录已过期，请重新登录');}
    const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'请求参数无效');return result;
  };
  const enqueue=async(app,path,body)=>{
    const m=models.get(app);try{const result=await api(path,{method:'POST',body:JSON.stringify(body||{})});m.trackedJobs.add(result.jobId);toast(app,m.demo?'模拟任务已提交':'任务已提交','info');await load(app);setTimeout(()=>load(app),1200);}catch(error){toast(app,error.message,'error');}
  };
  const load=async app=>{
    const m=models.get(app);if(m.loading)return;m.loading=true;
    try{
      const started=performance.now(),snapshot=await api('/api/snapshot');m.containers=snapshot.containers.sort((a,b)=>a.name.localeCompare(b.name,'zh-CN',{numeric:true,sensitivity:'base'}));m.host=snapshot.host;m.demo=!!snapshot.demo;
      if(!m.firstLoadLogged){console.info('Docker Monitor: first data loaded in '+Math.round(performance.now()-started)+' ms');m.firstLoadLogged=true;}
      m.checkCron=snapshot.policy.checkCron;m.updateCron=snapshot.policy.updateCron;m.updateMode=snapshot.policy.updateMode;m.waitValue=snapshot.policy.waitValue;m.waitUnit=snapshot.policy.waitUnit;
      m.lastCheck=snapshot.lastCheck?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(snapshot.lastCheck*1000)):'尚未检查';
      const banner=app.querySelector('[data-connection]');banner.hidden=true;app.dataset.connected='true';app.querySelector('.dw-demo').textContent=m.demo?'本地演示':'Docker 已连接';
      for(const job of snapshot.jobs){if(m.trackedJobs.has(job.id)&&!['queued','running'].includes(job.status)){m.trackedJobs.delete(job.id);toast(app,job.message,job.status==='success'?'success':'error');}}
      render(app);
      app.querySelector('[data-check-all]').disabled=snapshot.checking;app.querySelector('[data-check-all]').classList.toggle('dw-checking',snapshot.checking);
    }catch(error){const banner=app.querySelector('[data-connection]');banner.hidden=false;banner.textContent=error.message+' · 正在重试';app.dataset.connected='false';app.querySelector('.dw-demo').textContent='连接已断开';}
    finally{m.loading=false;}
  };

  const shell = app => {
    app.innerHTML=`<header class="dw-chrome"><div class="dw-logo"><img src="/icons/icon.svg?v=a2" alt="Docker Monitor"></div><div class="dw-brand">Docker Monitor<small>容器运行监控</small></div><div class="dw-chrome-right"><span class="dw-demo">连接中…</span><div class="dw-theme-toggle" role="group" aria-label="外观模式"><button type="button" class="dw-theme-button" data-theme-mode="light" aria-label="浅色模式" data-tooltip="浅色模式">${icons('sun')}<span>浅色</span></button><button type="button" class="dw-theme-button" data-theme-mode="dark" aria-label="暗色模式" data-tooltip="暗色模式">${icons('moon')}<span>暗色</span></button><button type="button" class="dw-theme-button" data-theme-mode="auto" aria-label="自动跟随系统" data-tooltip="跟随系统自动切换">${icons('monitor')}<span>自动</span></button></div><span class="dw-avatar">AD</span></div></header>
    <main class="dw-main"><div class="dw-connection" data-connection role="alert">正在连接 Docker…</div><div class="dw-heading"><div><h1>容器概览</h1><p class="dw-subtitle">运行状态、资源占用与镜像更新</p></div><div class="dw-host">${icons('server')}<div><strong>Docker 主机</strong><small>连接中…</small></div><span class="dw-dot" aria-label="连接正常"></span></div></div>
    <div class="dw-kpis"><button type="button" class="dw-kpi" data-filter="all" aria-label="查看全部容器"><span class="dw-kpi-symbol">${icons('boxes')}</span><div class="dw-kpi-content"><div class="dw-kpi-head">全部容器</div><div class="dw-value" data-kpi="total">8</div><div class="dw-kpi-foot">当前 Docker 主机</div></div></button><button type="button" class="dw-kpi" data-filter="running" aria-label="查看运行中的容器"><span class="dw-kpi-symbol">${icons('circle-play')}</span><div class="dw-kpi-content"><div class="dw-kpi-head">运行中</div><div class="dw-value" data-kpi="running">7<small>/ 8</small></div><div class="dw-kpi-foot dw-health-color"><span class="dw-dot"></span><span data-kpi="run-note">服务正在运行</span></div></div></button><div class="dw-kpi"><span class="dw-kpi-symbol">${icons('cpu')}</span><div class="dw-kpi-content"><div class="dw-kpi-head">CPU 总占用</div><div class="dw-value" data-kpi="cpu">18.4<small>%</small></div><div class="dw-kpi-foot">8 核处理器</div></div></div><div class="dw-kpi"><span class="dw-kpi-symbol">${icons('memory-stick')}</span><div class="dw-kpi-content"><div class="dw-kpi-head">内存占用</div><div class="dw-value" data-kpi="memory">3.84<small>GB</small></div><div class="dw-kpi-foot">主机内存 16 GB</div></div></div></div>

    <div class="dw-section-heading"><h2>我的容器<span class="dw-count" data-kpi="count">8</span></h2><div class="dw-section-tools"><button type="button" class="dw-btn dw-batch-update" data-update-all hidden>${icons('circle-arrow-up')}<span></span></button><span class="dw-live"><span class="dw-dot"></span><span data-update>实时数据</span></span><div class="dw-view-toggle" role="group" aria-label="容器展示模式"><button type="button" class="dw-view-button" data-view="list" aria-pressed="true">${icons('list')}列表</button><button type="button" class="dw-view-button" data-view="cards" aria-pressed="false">${icons('layout-grid')}卡片</button></div></div></div><div class="dw-toolbar"><div class="dw-tabs" role="group" aria-label="筛选容器状态"><button type="button" class="dw-tab" data-filter="all">全部 <span data-filter-count="all">8</span></button><button type="button" class="dw-tab" data-filter="running">运行中 <span data-filter-count="running">7</span></button><button type="button" class="dw-tab" data-filter="stopped">已停止 <span data-filter-count="stopped">1</span></button><button type="button" class="dw-tab" data-filter="warning">异常 <span data-filter-count="warning">1</span></button></div><label class="dw-search">${icons('search')}<input type="search" placeholder="搜索容器或镜像…" aria-label="搜索容器或镜像"></label></div><div data-list></div><footer class="dw-footer"><span>${icons('shield-check')}仅开放启动、停止、重启</span><span>${icons('flask-conical')}操作仅模拟，不影响实际容器</span></footer></main>
    <div class="dw-overlay" hidden><section class="dw-dialog" role="dialog" aria-modal="true" aria-label="容器操作确认"></section></div><div class="dw-toast" role="status" aria-live="polite" hidden></div>`;
    const overview=document.createElement('div');overview.className='dw-overview';
    const kpis=app.querySelector('.dw-kpis');
    kpis.before(overview);overview.append(kpis);
    app.querySelector('.dw-footer').remove();
    const controls=app.querySelector('.dw-section-tools');
    controls.insertAdjacentHTML('afterbegin',`<button type="button" class="dw-btn" data-check-all>${icons('refresh-cw')}检查更新</button><button type="button" class="dw-btn" data-policy>${icons('sliders-horizontal')}更新策略</button><button type="button" class="dw-btn" data-notifications>${icons('bell')}通知</button>`);
  };
  // Reconcile in place so polling and task feedback don't destroy focused controls,
  // or temporarily shrink the page's scroll range.
  const patchNode=(current,next)=>{
    if(current.nodeType!==next.nodeType){current.replaceWith(next);return;}
    if(next.nodeType===Node.TEXT_NODE){if(current.nodeValue!==next.nodeValue)current.nodeValue=next.nodeValue;return;}
    if(next.nodeType!==Node.ELEMENT_NODE)return;
    const icon=next.getAttribute('data-lucide');
    if(icon&&current.getAttribute('data-lucide')===icon)return;
    if(current.tagName!==next.tagName){current.replaceWith(next);return;}
    for(const attr of [...current.attributes])if(!next.hasAttribute(attr.name))current.removeAttribute(attr.name);
    for(const attr of next.attributes)if(current.getAttribute(attr.name)!==attr.value)current.setAttribute(attr.name,attr.value);
    const oldChildren=[...current.childNodes],newChildren=[...next.childNodes];
    for(let i=0;i<Math.max(oldChildren.length,newChildren.length);i++){
      if(!oldChildren[i])current.append(newChildren[i]);
      else if(!newChildren[i])oldChildren[i].remove();
      else patchNode(oldChildren[i],newChildren[i]);
    }
  };
  const setList=(app,markup)=>{
    const m=models.get(app),list=app.querySelector('[data-list]');if(m.listMarkup===markup)return;
    const y=window.scrollY,x=window.scrollX;
    const template=document.createElement('template');template.innerHTML=markup;
    const next=template.content.firstElementChild,current=list.firstElementChild;
    const oldIds=[...list.querySelectorAll('[data-container-id]')].map(el=>el.dataset.containerId).join('|');
    const nextIds=[...template.content.querySelectorAll('[data-container-id]')].map(el=>el.dataset.containerId).join('|');
    const sameCollection=oldIds===nextIds&&current?.className===next?.className;
    if(current&&next&&sameCollection)patchNode(current,next);else list.replaceChildren(template.content);
    m.listMarkup=markup;
    if(sameCollection&&!document.body.classList.contains('dw-modal-open'))window.scrollTo(x,y);
  };
  const render = app => {
    const m=models.get(app), cs=m.containers;
    for(const el of app.querySelectorAll('[data-view]'))el.setAttribute('aria-pressed',String(el.dataset.view===app.dataset.layout));
    const counts={all:cs.length,running:cs.filter(c=>c.state!=='stopped').length,stopped:cs.filter(c=>c.state==='stopped').length,warning:cs.filter(c=>c.state==='warning').length};
    const cpu=cs.reduce((sum,c)=>sum+(c.state==='stopped'?0:c.cpu),0), mem=cs.reduce((sum,c)=>sum+(c.state==='stopped'?0:c.memory),0);
    app.querySelector('[data-kpi="total"]').textContent=cs.length;
    app.querySelector('[data-kpi="running"]').innerHTML=`${counts.running}<small>/ ${cs.length}</small>`;
    app.querySelector('[data-kpi="cpu"]').innerHTML=`${cpu.toFixed(1)}<small>%</small>`;
    app.querySelector('[data-kpi="memory"]').innerHTML=`${(mem/1024).toFixed(2)}<small>GB</small>`;
    for(const el of app.querySelectorAll('[data-filter-count]'))el.textContent=counts[el.dataset.filterCount];
    for(const el of app.querySelectorAll('[data-filter]'))el.setAttribute('aria-pressed',String(el.dataset.filter===m.filter));
    const visible=cs.filter(c=>(m.filter==='all'||(m.filter==='running'?c.state!=='stopped':c.state===m.filter))&&`${c.name} ${c.image}`.toLowerCase().includes(m.search.toLowerCase()));
    app.querySelector('[data-kpi="count"]').textContent=visible.length;
    const empty='<div class="dw-empty">没有找到符合条件的容器</div>';
    if(app.dataset.layout==='cards'){
      setList(app,`<div class="dw-cards">${visible.length?visible.map(c=>`<article data-container-id="${safe(c.id)}" class="dw-service ${c.update==='available'&&!c.protected?'has-update':''}"><div class="dw-service-top"><span class="dw-container-icon ${c.self?'dw-program-icon':''}">${c.self?'<img src="/icons/icon.svg?v=a2" alt="Docker Monitor">':icons(c.icon)}</span>${name(c)}${badge(c)}</div><div class="dw-service-metrics"><div><small>CPU 占用</small><strong>${c.state==='stopped'||c.metricsAvailable===false?'—':c.cpu.toFixed(1)+'%'}</strong></div><div><small>内存</small><strong>${c.state==='stopped'||c.metricsAvailable===false?'—':memoryLabel(c.memory)}</strong></div></div>${c.protected?`<div class="dw-update-strip">${updateStatus(c)}<span>自动更新 · 不适用</span></div>`:`<div class="dw-update-strip">${updateStatus(c)}<button type="button" class="dw-auto-button ${c.auto?'enabled':''}" data-policy aria-label="设置 ${safe(c.name)} 自动更新">自动更新 · ${c.auto?'已开':'关闭'}</button></div>`}<div class="dw-service-bottom"><span class="dw-time">${c.state==='stopped'?c.time:'运行 '+c.time}</span>${actions(c)}</div></article>`).join(''):empty}</div>`);
    }else{
      setList(app,visible.length?`<div class="dw-table-wrap"><table class="dw-table"><colgroup><col style="width:31%"><col style="width:12%"><col style="width:10%"><col style="width:13%"><col class="dw-time-col" style="width:12%"><col style="width:22%"></colgroup><thead><tr><th>容器 / 镜像</th><th>状态</th><th>CPU</th><th>内存</th><th class="dw-time-col">运行时长</th><th style="text-align:right">操作</th></tr></thead><tbody>${visible.map(c=>`<tr data-container-id="${safe(c.id)}" class="${c.update==='available'&&!c.protected?'has-update':''}"><td>${name(c,true)}</td><td>${badge(c)}</td><td><span class="dw-num">${c.state==='stopped'||c.metricsAvailable===false?'—':c.cpu.toFixed(1)+'%'}</span>${c.state==='stopped'?'':`<div class="dw-mini-track"><span style="width:${Math.min(100,c.cpu*5)}%"></span></div>`}</td><td class="dw-num">${c.state==='stopped'||c.metricsAvailable===false?'—':memoryLabel(c.memory)}</td><td class="dw-time-col"><span class="dw-time">${c.state==='stopped'?'—':c.time}</span></td><td>${actions(c)}</td></tr>`).join('')}</tbody></table></div>`:empty);
    }
    if(app.dataset.layout==='list'){
      const widths=[28,12,10,13,10,27];app.querySelectorAll('col').forEach((col,index)=>col.style.width=col.classList.contains('dw-time-col')&&app.clientWidth<1050?'0':widths[index]+'%');
    }
    app.querySelector('[data-update]').textContent=m.lastCheck==='尚未检查'?'上次检查：—':'上次检查：'+m.lastCheck;

    if(!cs.some(c=>c.metricsAvailable)){
      app.querySelector('[data-kpi="cpu"]').textContent='—';
      app.querySelector('[data-kpi="memory"]').textContent='—';
    }
    const updates=batchCandidates(m).length;
    app.querySelector('[data-update-all]').hidden=!updates;
    app.querySelector('[data-update-all] span').textContent='一键更新 · '+updates;
    if(m.host){
      app.querySelector('.dw-host strong').textContent=m.host.name;
      app.querySelector('.dw-host small').textContent='Docker '+m.host.version+' · 本机';
      app.querySelector('[data-kpi="cpu"]').nextElementSibling.textContent=m.host.cpus+' 核处理器';
      app.querySelector('[data-kpi="memory"]').nextElementSibling.textContent='主机内存 '+(m.host.memory/1073741824).toFixed(2)+' GB';
    }
    for(const c of cs)if(c.busy)for(const button of app.querySelectorAll('[data-id="'+c.id+'"],[data-check="'+c.id+'"]'))button.disabled=true;
    refreshIcons();
  };
  const animateList=app=>{
    const list=app.querySelector('[data-list]');list.classList.remove('dw-list-transition');
    void list.offsetWidth;list.classList.add('dw-list-transition');
  };
  const tap=button=>{
    button.classList.remove('dw-click-feedback');void button.offsetWidth;button.classList.add('dw-click-feedback');
    setTimeout(()=>button.classList.remove('dw-click-feedback'),450);
  };
  const toast=(app,message,type='success')=>{
    const m=models.get(app),el=app.querySelector('.dw-toast');
    clearTimeout(m.timer);el.dataset.type=type;el.innerHTML=icons(type==='error'?'triangle-alert':type==='info'?'info':'circle-check')+safe(message);el.hidden=false;refreshIcons();
    m.timer=setTimeout(()=>el.hidden=true,type==='error'?5500:3500);
  };
  const openOverlay=app=>{
    const m=models.get(app);m.scrollY=window.scrollY;
    document.body.style.top=-m.scrollY+'px';document.body.classList.add('dw-modal-open');
    const overlay=app.querySelector('.dw-overlay');overlay.hidden=false;overlay.scrollTop=0;
  };
  const close=app=>{
    app.querySelector('.dw-overlay').hidden=true;const m=models.get(app);m.pending=null;
    document.body.classList.remove('dw-modal-open');document.body.style.top='';void document.body.offsetHeight;window.scrollTo(0,m.scrollY||0);
    (m.lastFocus?.isConnected?m.lastFocus:app.querySelector('input')).focus({preventScroll:true});
  };
  const stamp=()=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date());

  const batchCandidates=m=>m.containers.filter(c=>c.update==='available'&&!c.protected&&!c.busy&&c.state==='running');
  const batchDialog=app=>{
    const m=models.get(app),selected=batchCandidates(m);if(!selected.length)return;
    m.lastFocus=document.activeElement;m.pending={action:'update-all',ids:selected.map(c=>c.id)};openOverlay(app);
    const box=app.querySelector('.dw-dialog');box.classList.remove('policy');box.setAttribute('aria-label','一键更新确认');
    box.innerHTML=`<div class="dw-dialog-icon">${icons('circle-arrow-up')}</div><h3>更新这 ${selected.length} 个容器？</h3><p>逐个更新当前标签的镜像，保留配置和挂载。服务会短暂中断，失败时尝试恢复原容器。${m.demo?'<br>本地演示，仅模拟更新。':''}</p><div class="dw-batch-list">${selected.map(c=>`<div>${safe(c.name)}</div>`).join('')}</div><div class="dw-dialog-buttons"><button type="button" class="dw-btn" data-close>取消</button><button type="button" class="dw-btn primary" data-confirm>确认更新</button></div>`;
    refreshIcons();box.querySelector('button').focus({preventScroll:true});
  };
  const checkUpdates=(app,id)=>enqueue(app,id?'/api/containers/'+id+'/actions':'/api/check-updates',id?{action:'check'}:{});

  const validateCron=box=>{
    let valid=true;
    const descriptions={'0 */6 * * *':'每 6 小时','0 3 * * *':'每天 03:00','0 * * * *':'每小时','0 3 * * 0':'每周日 03:00'};
    for(const input of box.querySelectorAll('[data-cron]:not(:disabled)')){
      const hint=input.parentElement.querySelector('.dw-cron-hint');
      try{
        const expr=input.value.trim();if(expr.split(/\s+/).length!==5)throw new Error('请输入五位 Cron：分 时 日 月 周');
        if(!globalThis.Cron)throw new Error('表达式组件加载失败，请刷新');
        const schedule=new Cron(expr,{timezone:'Asia/Shanghai',paused:true});
        const next=schedule.nextRun();schedule.stop();
        if(!next)throw new Error('此表达式没有下一次执行时间');
        const text=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(next);
        hint.textContent=(descriptions[expr]||'自定义计划')+' · 下次 '+text;hint.classList.remove('invalid');input.setAttribute('aria-invalid','false');
      }catch(err){valid=false;hint.textContent=err.message.startsWith('Cron')?'表达式无效，请检查数值、范围和步长':err.message;hint.classList.add('invalid');input.setAttribute('aria-invalid','true');}
    }
    const wait=box.querySelector('[data-wait-value]');
    if(wait)wait.max=box.querySelector('[data-wait-unit]').value==='minutes'?'43200':'720';
    if(wait&&!wait.disabled){const invalid=!wait.checkValidity()||wait.value==='';if(invalid)valid=false;wait.setAttribute('aria-invalid',String(invalid));const hint=box.querySelector('[data-wait-hint]');if(hint){hint.textContent=invalid?'请输入 0–'+wait.max+' 的整数':'0 表示发现后立即更新';hint.classList.toggle('invalid',invalid);}}
    for(const button of box.querySelectorAll('[data-save-policy],[data-auto-run]'))button.disabled=!valid;
    return valid;
  };
  const updatePolicyMode=box=>{
    const delayed=box.querySelector('[data-update-mode]').value==='delay';
    for(const field of box.querySelectorAll('[data-mode-field]')){const active=field.dataset.modeField===(delayed?'delay':'scheduled');field.hidden=!active;for(const control of field.querySelectorAll('input,select,button'))control.disabled=!active;}
    box.querySelector('[data-policy-note]').textContent=delayed?'首次检测到更新后开始计时，等待结束即执行。':'检测到更新后，在下一个计划时间执行。';

    validateCron(box);
  };
  const policy=app=>{
    const m=models.get(app);m.lastFocus=document.activeElement;m.pending={action:'policy'};
    const overlay=app.querySelector('.dw-overlay'),box=overlay.querySelector('.dw-dialog');
    openOverlay(app);box.classList.add('policy');box.setAttribute('aria-label','自动更新策略');
    box.innerHTML=`<div class="dw-policy-header"><div class="dw-dialog-icon">${icons('calendar-clock')}</div><div><h3>自动更新策略</h3><p>北京时间 · ${m.demo?'本地演示':'实际执行'}</p></div><div class="dw-policy-header-actions"><button type="button" class="dw-btn" data-close>取消</button><button type="button" class="dw-btn primary" data-save-policy>保存策略</button></div></div>
      <div class="dw-policy-section-title">调度计划<small>Cron · 分 时 日 月 周</small></div>
      <div class="dw-policy-form"><label>检测频率 · Cron<input type="text" data-cron="check" aria-label="检测频率 Cron" value="${safe(m.checkCron)}" spellcheck="false"><span class="dw-cron-hint" aria-live="polite"></span></label><label>更新方式<select data-update-mode aria-label="自动更新方式"><option value="scheduled" ${m.updateMode==='scheduled'?'selected':''}>按固定时间更新</option><option value="delay" ${m.updateMode==='delay'?'selected':''}>发现后延迟更新</option></select><span class="dw-cron-hint">选择一种更新方式</span></label><label data-mode-field="scheduled">更新时间 · Cron<input type="text" data-cron="update" aria-label="自动更新时间 Cron" value="${safe(m.updateCron)}" spellcheck="false"><span class="dw-cron-hint" aria-live="polite"></span></label><div data-mode-field="delay" class="dw-wait-field"><span>发现后等待</span><div class="dw-wait-control"><div class="dw-stepper"><button type="button" data-wait-step="-1" aria-label="减少等待时间">${icons('minus')}</button><input type="number" data-wait-value aria-label="首次发现后等待时间" min="0" max="${m.waitUnit==='minutes'?43200:720}" step="1" value="${m.waitValue}"><button type="button" data-wait-step="1" aria-label="增加等待时间">${icons('plus')}</button></div><select data-wait-unit aria-label="等待时间单位"><option value="minutes" ${m.waitUnit==='minutes'?'selected':''}>分钟</option><option value="hours" ${m.waitUnit==='hours'?'selected':''}>小时</option></select></div><span class="dw-cron-hint" data-wait-hint aria-live="polite"></span></div></div>
      <div class="dw-policy-note" data-policy-note aria-live="polite"></div>
      <div class="dw-policy-section-title">参与自动更新的容器<small data-auto-count>已开启 ${m.containers.filter(c=>c.auto&&!c.protected).length} / ${m.containers.filter(c=>!c.protected).length}</small></div>
      <div class="dw-policy-list">${m.containers.map(c=>`<label class="dw-policy-row"><span class="dw-policy-identity"><span class="dw-container-icon ${c.self?'dw-program-icon':''}">${c.self?'<img src="/icons/icon.svg?v=a2" alt="Docker Monitor">':icons(c.icon)}</span><strong data-tooltip="${safe(c.name)}">${safe(c.name)}</strong></span><span class="dw-policy-state ${c.protected?'':c.state}">${c.protected?icons('shield-check'):'<span class="dw-dot"></span>'}${c.protected?'受保护':c.state==='stopped'?'已停止 · 跳过':c.state==='warning'?'异常 · 跳过':'运行中'}</span><input type="checkbox" role="switch" class="cursor-interaction" data-auto-id="${c.id}" aria-label="${safe(c.name)} 自动更新" ${c.auto?'checked':''} ${c.protected?'disabled':''}></label>`).join('')}</div>
      `;
    updatePolicyMode(box);refreshIcons();box.querySelector('[data-cron]').focus({preventScroll:true});
  };

  const savePolicy=async app=>{
    const m=models.get(app),box=app.querySelector('.dw-dialog');if(!validateCron(box))return false;
    const policy={checkCron:box.querySelector('[data-cron="check"]').value.trim(),updateMode:box.querySelector('[data-update-mode]').value,
      updateCron:box.querySelector('[data-cron="update"]').disabled?m.updateCron:box.querySelector('[data-cron="update"]').value.trim(),
      waitValue:box.querySelector('[data-wait-value]').disabled?m.waitValue:Number(box.querySelector('[data-wait-value]').value),
      waitUnit:box.querySelector('[data-wait-unit]').disabled?m.waitUnit:box.querySelector('[data-wait-unit]').value,
      auto:[...box.querySelectorAll('[data-auto-id]:checked:not(:disabled)')].map(input=>m.containers.find(c=>c.id===input.dataset.autoId)?.name).filter(Boolean)};
    const button=box.querySelector('[data-save-policy]');button.disabled=true;
    try{await api('/api/policy',{method:'PUT',body:JSON.stringify(policy)});Object.assign(m,policy);return true;}
    catch(error){toast(app,error.message,'error');return false;}finally{button.disabled=false;}
  };

  const notificationValues=box=>({enabled:box.querySelector('[data-notify-enabled]').checked,
    endpoint:box.querySelector('[data-notify-endpoint]').value.trim(),siteUrl:box.querySelector('[data-notify-site]').value.trim(),
    iconUrl:box.querySelector('[data-notify-icon]').value.trim(),onAvailable:box.querySelector('[data-notify-available]').checked,
    onSuccess:box.querySelector('[data-notify-success]').checked,onFailure:box.querySelector('[data-notify-failure]').checked});
  const notificationDialog=async app=>{
    const m=models.get(app);let value;try{value=await api('/api/notifications');}catch(e){toast(app,e.message,'error');return;}
    m.lastFocus=document.activeElement;openOverlay(app);const box=app.querySelector('.dw-dialog');box.classList.add('policy');box.setAttribute('aria-label','Bark 通知设置');
    box.innerHTML=`<div class="dw-policy-header"><div class="dw-dialog-icon">${icons('bell')}</div><div><h3>Bark 通知</h3><p>${m.demo?'本地模拟 · 不发送真实通知':'镜像更新提醒'}</p></div><div class="dw-policy-header-actions"><button class="dw-btn" type="button" data-close>取消</button><button class="dw-btn primary" type="button" data-save-notifications>保存</button></div></div>
    <div class="dw-notify-body"><label class="dw-notify-toggle"><span>启用通知<small>发送到你的 Bark 设备</small></span><input type="checkbox" role="switch" data-notify-enabled ${value.enabled?'checked':''}></label>
    <label>Bark 推送地址<input type="password" data-notify-endpoint autocomplete="off" placeholder="${value.configured?'已配置 · 留空保留原地址':'https://api.day.app/你的设备Key'}"><small>${value.configured?'当前服务：'+safe(value.server)+'；设备 Key 已隐藏。':'复制 Bark 应用中的完整推送地址。'}</small></label>
    <label>程序访问地址<input type="url" data-notify-site value="${safe(value.siteUrl||location.origin)}" placeholder="https://monitor.example.com"><small>用于点击通知打开程序，也用于生成默认图标地址。</small></label>
    <label>通知图标<input type="url" data-notify-icon value="${safe(value.iconUrl||'')}" placeholder="默认使用本程序图标"><small>默认使用程序访问地址下的图标；手机需要能访问该地址，外网通知建议使用 HTTPS 公网地址。</small></label>
    <div class="dw-notify-events"><label><input type="checkbox" data-notify-available ${value.onAvailable?'checked':''}>发现更新</label><label><input type="checkbox" data-notify-success ${value.onSuccess?'checked':''}>更新成功</label><label><input type="checkbox" data-notify-failure ${value.onFailure?'checked':''}>更新失败</label></div>
    <div class="dw-notify-test"><button type="button" class="dw-btn" data-test-notifications>${icons('send')}发送测试</button><small>测试会发送到当前填写的设备，无需先保存。</small></div><p class="dw-notify-result" role="status">${safe(value.lastDelivery?.message||'尚未发送通知')}</p></div>`;
    refreshIcons();box.querySelector('button').focus({preventScroll:true});
  };
  const notificationSubmit=async(app,test)=>{
    const box=app.querySelector('.dw-dialog'),button=box.querySelector(test?'[data-test-notifications]':'[data-save-notifications]'),result=box.querySelector('.dw-notify-result');button.disabled=true;
    try{const response=await api(test?'/api/notifications/test':'/api/notifications',{method:test?'POST':'PUT',body:JSON.stringify(notificationValues(box))});
      if(test){result.textContent=response.message;toast(app,response.message);}else{close(app);toast(app,models.get(app).demo?'已保存本地模拟通知设置':'通知设置已保存');}
    }catch(e){result.textContent=e.message;toast(app,e.message,'error');}finally{button.disabled=false;}
  };

  const dialog=(app,c,action)=>{
    const m=models.get(app);m.lastFocus=document.activeElement;m.pending={id:c.id,action};
    const overlay=app.querySelector('.dw-overlay'),box=overlay.querySelector('.dw-dialog');
    openOverlay(app);
    box.classList.remove('policy');
    {
      const labels={start:'启动',stop:'停止',restart:'重启',update:'更新'};
      box.setAttribute('aria-label',labels[action]+'容器确认');
      box.innerHTML=`<div class="dw-dialog-icon ${action==='stop'?'danger':''}">${icons(action==='stop'?'square':action==='start'?'play':action==='update'?'download':'rotate-cw')}</div><h3>${labels[action]}这个容器？</h3><p>${action==='stop'?'停止后，该容器提供的服务将暂时不可用。':action==='restart'?'重启期间，该容器提供的服务会短暂中断。':action==='update'?'更新当前标签的镜像并重建容器，保留挂载和配置，失败时尝试恢复原容器。服务可能短暂中断。':'启动后，该容器将恢复运行。'}${m.demo?'<br>本地演示，仅模拟状态变化。':''}</p><div class="dw-dialog-label">${safe(c.name)}<small data-tooltip="${safe(c.image)}">${safe(c.image)}</small></div><div class="dw-dialog-buttons"><button type="button" class="dw-btn" data-close>取消</button><button type="button" class="dw-btn ${action==='stop'?'danger':'primary'}" data-confirm>确认${labels[action]}</button></div>`;
    }
    refreshIcons();box.querySelector('button').focus({preventScroll:true});
  };
  for(const app of root.querySelectorAll('.dw-app')){
    const m=restore(app);models.set(app,m);shell(app);applyTheme(app);app.querySelector('input').value=m.search;render(app);
    app.addEventListener('input',e=>{if(e.target.matches('input[type="search"]')){m.search=e.target.value;render(app);persist();}else if(e.target.matches('[data-update-mode]'))updatePolicyMode(app.querySelector('.dw-dialog'));else if(e.target.closest('.dw-policy-form'))validateCron(app.querySelector('.dw-dialog'));else if(e.target.matches('[data-auto-id]')){const box=app.querySelector('.dw-dialog');box.querySelector('[data-auto-count]').textContent=`已开启 ${box.querySelectorAll('[data-auto-id]:checked:not(:disabled)').length} / ${m.containers.filter(c=>!c.protected).length}`;}});
    app.addEventListener('click',async e=>{
      const step=e.target.closest('[data-wait-step]');if(step){const box=step.closest('.dw-dialog'),input=box.querySelector('[data-wait-value]');input.value=Math.max(0,Math.min(Number(input.max),(Number(input.value)||0)+Number(step.dataset.waitStep)));validateCron(box);return;}
      const target=e.target.closest('button');if(!target||target.disabled)return;tap(target);
      if(target.hasAttribute('data-theme-mode')){m.theme=target.dataset.themeMode;applyTheme(app);persist();}
      if(target.hasAttribute('data-policy'))policy(app);
      if(target.hasAttribute('data-check-all'))checkUpdates(app);
      if(target.hasAttribute('data-check'))checkUpdates(app,target.dataset.check);

      if(target.hasAttribute('data-notifications'))await notificationDialog(app);
      if(target.hasAttribute('data-save-notifications'))await notificationSubmit(app,false);
      if(target.hasAttribute('data-test-notifications'))await notificationSubmit(app,true);
      if(target.hasAttribute('data-update-all'))batchDialog(app);
      if(target.hasAttribute('data-save-policy')){if(!await savePolicy(app))return;close(app);await load(app);toast(app,m.demo?'已保存本地模拟策略':'更新策略已保存并生效');}

      if(target.hasAttribute('data-filter')){m.filter=target.dataset.filter;render(app);animateList(app);persist();}
      if(target.hasAttribute('data-view')){app.dataset.layout=target.dataset.view;render(app);animateList(app);persist();}
      if(target.hasAttribute('data-close'))close(app);
      if(target.hasAttribute('data-action')){const c=m.containers.find(c=>c.id===target.dataset.id);if(c&&!c.protected)dialog(app,c,target.dataset.action);}

      if(target.hasAttribute('data-confirm')&&m.pending){const {id,action,ids}=m.pending;target.disabled=true;close(app);await enqueue(app,action==='update-all'?'/api/update-all':'/api/containers/'+id+'/actions',action==='update-all'?{ids}:{action});}
    });
    const logout=document.createElement('button');logout.type='button';logout.className='dw-logout';logout.hidden=true;logout.setAttribute('aria-label','退出登录');logout.innerHTML=icons('log-out')+'<span>退出</span>';
    app.querySelector('.dw-avatar').replaceWith(logout);
    api('/api/auth/session').then(session=>{logout.hidden=!session.enabled;logout.title=session.username?'退出 '+session.username:'退出登录';}).catch(()=>{});
    logout.addEventListener('click',async()=>{logout.disabled=true;try{await api('/api/auth/logout',{method:'POST'});window.location.replace('/login');}catch(error){logout.disabled=false;toast(app,error.message,'error');}});
    load(app);setInterval(()=>{if(!document.hidden)load(app);},5000);

    app.querySelector('.dw-overlay').addEventListener('click',e=>{if(e.target.classList.contains('dw-overlay'))close(app);});
    app.addEventListener('keydown',e=>{
      const overlay=app.querySelector('.dw-overlay');if(overlay.hidden)return;
      if(e.key==='Escape')close(app);
      if(e.key==='Tab'){const controls=[...overlay.querySelectorAll('button,input:not(:disabled),select')],first=controls[0],last=controls[controls.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
    });
    if(globalThis.Tweak){const tweak=new Tweak({container:app,onChange:()=>{app.style.setProperty('--dw-radius',m.design.radius+'px');app.style.setProperty('--dw-row-pad',m.design.compact?'10px':'14px');}});tweak.addSlider(m.design,'radius',{label:'窗口圆角',min:4,max:24,unit:'px'});tweak.addToggle(m.design,'compact',{label:'紧凑列表'});}
  }

})();