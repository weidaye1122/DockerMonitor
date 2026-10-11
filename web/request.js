(() => {
  let token=null,pending=null;
  const csrf=async()=>{
    if(token)return token;
    if(!pending)pending=(async()=>{
      const response=await fetch('/api/auth/csrf',{cache:'no-store',credentials:'same-origin',
        headers:{'X-Monitor-Request':'1','X-Monitor-Client-Scheme':location.protocol.slice(0,-1)},signal:AbortSignal.timeout(15000)});
      const result=await response.json();
      if(!response.ok||!result.token)throw new Error(result.detail||'页面验证失败，请重试');
      return token=result.token;
    })().finally(()=>{pending=null;});
    return pending;
  };
  globalThis.monitorFetch=async(path,options={})=>{
    const mutation=!['GET','HEAD','OPTIONS'].includes((options.method||'GET').toUpperCase());
    const send=async()=>{
      const headers=new Headers(options.headers);
      headers.set('X-Monitor-Request','1');
      if(mutation)headers.set('X-CSRF-Token',await csrf());
      return fetch(path,{...options,headers,credentials:'same-origin'});
    };
    let response=await send();
    if(mutation&&response.status===403){
      const result=await response.clone().json().catch(()=>({}));
      // Only retry an explicitly rejected CSRF validation: no operation ran.
      if(result.code==='csrf_invalid'){token=null;response=await send();}
    }
    return response;
  };
})();
