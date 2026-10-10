"""Bring the approved prototype's UI into the app; no iframe or host-only runtime."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "design/prototype.html"
source = SOURCE.read_text()
css = re.search(r"<style>(.*?)</style>", source, re.S).group(1)
script = source[source.index("(() => {"):source.rindex("</script>")].strip()


def section(start, end, replacement):
    global script
    left, right = script.index(start), script.index(end)
    script = script[:left] + replacement + "\n" + script[right:]


def replace(old, new):
    global script
    if old not in script:
        raise RuntimeError("Prototype changed: " + old[:80])
    script = script.replace(old, new)


section("  const initial = [", "  const icons =", "  const initial = [];")
replace("  const saved = window.openai?.widgetState?.privateContent;", "  let saved;try{saved=JSON.parse(localStorage.getItem('docker-monitor-ui')||'{}');}catch{saved={};}")
section("  const restore = app => {", "  const shell = app => {", """
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
""")
replace("<span class=\"dw-demo\">演示模式</span>", "<span class=\"dw-demo\">连接中…</span>")
replace('<div class="dw-section-tools">', '<div class="dw-section-tools"><button type="button" class="dw-btn dw-batch-update" data-update-all hidden>'+"${icons('circle-arrow-up')}"+'<span></span></button>')
replace("Home NAS", "Docker 主机")
replace("192.168.1.100 · 本机 Docker", "连接中…")
replace("模拟数据", "实时数据")
replace("<main class=\"dw-main\">", '<main class="dw-main"><div class="dw-connection" data-connection role="alert">正在连接 Docker…</div>')
replace('<strong>${safe(c.name)}</strong>', '<strong data-tooltip="${safe(c.name)}">${safe(c.name)}</strong>')
replace('<small>${safe(c.image)}</small>', '<small data-tooltip="${safe(c.image)}">${safe(c.image)}</small>')
replace("    refreshIcons();\n  };\n  const animateList", """
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
  const animateList""")
section("  const checkUpdates=(app,id)=>{", "  const validateCron=box=>{", """
  const batchCandidates=m=>m.containers.filter(c=>c.update==='available'&&!c.protected&&!c.busy&&c.state==='running');
  const batchDialog=app=>{
    const m=models.get(app),selected=batchCandidates(m);if(!selected.length)return;
    m.lastFocus=document.activeElement;m.pending={action:'update-all',ids:selected.map(c=>c.id)};openOverlay(app);
    const box=app.querySelector('.dw-dialog');box.classList.remove('policy');box.setAttribute('aria-label','一键更新确认');
    box.innerHTML=`<div class="dw-dialog-icon">${icons('circle-arrow-up')}</div><h3>更新这 ${selected.length} 个容器？</h3><p>逐个更新当前标签的镜像，保留配置和挂载。服务会短暂中断，失败时尝试恢复原容器。${m.demo?'<br>本地演示，仅模拟更新。':''}</p><div class="dw-batch-list">${selected.map(c=>`<div>${safe(c.name)}</div>`).join('')}</div><div class="dw-dialog-buttons"><button type="button" class="dw-btn" data-close>取消</button><button type="button" class="dw-btn primary" data-confirm>确认更新</button></div>`;
    refreshIcons();box.querySelector('button').focus({preventScroll:true});
  };
  const checkUpdates=(app,id)=>enqueue(app,id?'/api/containers/'+id+'/actions':'/api/check-updates',id?{action:'check'}:{});
""")
replace("北京时间 · 演示模式", "北京时间 · ${m.demo?'本地演示':'实际执行'}")
section("  const savePolicy=app=>{", "  const dialog=(app,c,action)=>{", """
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
""")
replace("<br>当前为演示模式，仅模拟状态变化。", "${m.demo?'<br>本地演示，仅模拟状态变化。':''}")
replace("保留挂载和配置。服务可能短暂中断。", "保留挂载和配置，失败时尝试恢复原容器。服务可能短暂中断。")
script = script.replace("data-policy>${icons('sliders-horizontal')}更新策略</button>", "data-policy>${icons('sliders-horizontal')}更新策略</button><button type=\"button\" class=\"dw-btn\" data-notifications>${icons('bell')}通知</button>")
script = script.replace('  const dialog=(app,c,action)=>{', (ROOT / 'tools/notification_ui.js').read_text() + '\n  const dialog=(app,c,action)=>{')
replace("app.addEventListener('click',e=>{", "app.addEventListener('click',async e=>{")
section("      if(target.hasAttribute('data-save-policy'))", "      if(target.hasAttribute('data-filter'))", """
      if(target.hasAttribute('data-notifications'))await notificationDialog(app);
      if(target.hasAttribute('data-save-notifications'))await notificationSubmit(app,false);
      if(target.hasAttribute('data-test-notifications'))await notificationSubmit(app,true);
      if(target.hasAttribute('data-update-all'))batchDialog(app);
      if(target.hasAttribute('data-save-policy')){if(!await savePolicy(app))return;close(app);await load(app);toast(app,m.demo?'已保存本地模拟策略':'更新策略已保存并生效');}
""")
section("      if(target.hasAttribute('data-confirm')&&m.pending){", "    app.querySelector('.dw-overlay').addEventListener", """
      if(target.hasAttribute('data-confirm')&&m.pending){const {id,action,ids}=m.pending;target.disabled=true;close(app);await enqueue(app,action==='update-all'?'/api/update-all':'/api/containers/'+id+'/actions',action==='update-all'?{ids}:{action});}
    });
    const logout=document.createElement('button');logout.type='button';logout.className='dw-logout';logout.hidden=true;logout.setAttribute('aria-label','退出登录');logout.innerHTML=icons('log-out')+'<span>退出</span>';
    app.querySelector('.dw-avatar').replaceWith(logout);
    api('/api/auth/session').then(session=>{logout.hidden=!session.enabled;logout.title=session.username?'退出 '+session.username:'退出登录';}).catch(()=>{});
    logout.addEventListener('click',async()=>{logout.disabled=true;try{await api('/api/auth/logout',{method:'POST'});window.location.replace('/login');}catch(error){logout.disabled=false;toast(app,error.message,'error');}});
    load(app);setInterval(()=>{if(!document.hidden)load(app);},5000);
""")
section("  window.addEventListener('openai:set_globals'", "})();", "")
replace("value === 0 ? '—'", "value == null ? '—' : value === 0 ? '0 B'")
replace("const updateStatus = c =>", "const updateStatus = c => c.busy?`<span class=\"dw-update-status running-job\">${icons('loader-circle')}任务执行中…</span>`:c.update==='error'?`<span class=\"dw-update-status failed\" data-tooltip=\"${safe(c.updateError||'检查失败')}\">${icons('triangle-alert')}检查失败</span>`:")
replace("c.state==='stopped'?'—':c.cpu.toFixed(1)", "c.state==='stopped'||c.metricsAvailable===false?'—':c.cpu.toFixed(1)")
replace("c.state==='stopped'?'—':memoryLabel(c.memory)", "c.state==='stopped'||c.metricsAvailable===false?'—':memoryLabel(c.memory)")
replace("const refreshIcons = () => {", "const refreshIcons = () => { for(const el of root.querySelectorAll('[data-tooltip]'))el.title=el.dataset.tooltip;")

(ROOT / "web/app.css").write_text(css + "\nhtml,body{margin:0;min-height:100%;font-family:system-ui,sans-serif}\nbody{background:light-dark(#f5f6f8,#111318)}\n")
(ROOT / "web/app.js").write_text(script)
(ROOT / "web/index.html").write_text("""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>Docker Monitor</title><meta name="theme-color" content="#0071e3"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="default"><meta name="apple-mobile-web-app-title" content="Docker Monitor"><link rel="icon" href="/favicon.ico?v=a2" sizes="any"><link rel="icon" type="image/svg+xml" href="/icons/icon.svg?v=a2"><link rel="icon" type="image/png" sizes="32x32" href="/icons/icon-32.png?v=a2"><link rel="apple-touch-icon" sizes="180x180" href="/icons/icon-180.png?v=a2"><link rel="manifest" href="/manifest.webmanifest?v=a2"><link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/login.css"><script src="/vendor/lucide.js" defer></script><script src="/vendor/croner.js" defer></script><script src="/app.js" defer></script></head><body><div id="dock-preview"><div class="dw-app" data-layout="cards" aria-label="Docker Monitor 容器监控面板"></div></div></body></html>""")
print("Built web/index.html, app.css, app.js")
