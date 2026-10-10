(() => {
  const root=document.getElementById('dock-preview'),form=document.getElementById('login-form');
  const password=document.getElementById('password'),error=document.getElementById('login-error');
  const system=window.matchMedia('(prefers-color-scheme: dark)');
  let saved={};try{saved=JSON.parse(localStorage.getItem('docker-monitor-ui')||'{}')||{};}catch{}
  let theme=['light','dark','auto'].includes(saved.theme)?saved.theme:'auto';
  const apply=()=>{const value=theme==='auto'?(system.matches?'dark':'light'):theme;root.style.colorScheme=value;document.documentElement.style.colorScheme=value;for(const button of root.querySelectorAll('[data-theme]'))button.setAttribute('aria-pressed',String(button.dataset.theme===theme));};
  apply();system.addEventListener('change',apply);
  root.querySelectorAll('[data-theme]').forEach(button=>button.addEventListener('click',()=>{theme=button.dataset.theme;apply();try{saved=JSON.parse(localStorage.getItem('docker-monitor-ui')||'{}')||{};localStorage.setItem('docker-monitor-ui',JSON.stringify({...saved,theme}));}catch{}}));
  const icons=()=>globalThis.lucide?.createIcons();icons();
  const toggle=document.getElementById('toggle-password');
  toggle.addEventListener('click',()=>{const visible=password.type==='password';password.type=visible?'text':'password';toggle.setAttribute('aria-pressed',String(visible));toggle.setAttribute('aria-label',visible?'隐藏密码':'显示密码');toggle.innerHTML=`<i data-lucide="${visible?'eye-off':'eye'}" aria-hidden="true"></i>`;icons();password.focus();});
  form.addEventListener('submit',async event=>{
    event.preventDefault();const button=form.querySelector('[type=submit]');if(button.disabled)return;
    button.disabled=true;button.querySelector('span').textContent='登录中…';error.hidden=true;
    try{
      const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Request':'1'},body:JSON.stringify({username:form.elements.username.value,password:password.value}),signal:AbortSignal.timeout(15000)});
      const result=await response.json();if(!response.ok){error.textContent=result.detail||'登录失败，请重试';error.hidden=false;password.focus();password.select();return;}
      password.value='';window.location.replace('/');
    }catch{error.textContent='连接失败，请检查网络后重试';error.hidden=false;}
    finally{button.disabled=false;button.querySelector('span').textContent='登录';}
  });
})();
