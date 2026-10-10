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
