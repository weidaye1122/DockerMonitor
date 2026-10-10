"""Interactive UI preview. In-memory simulated containers; never connects to Docker."""
import asyncio
import copy
import time
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field
from typing import List, Literal


class Policy(BaseModel):
    checkCron: str = Field(max_length=128)
    updateMode: Literal["scheduled", "delay"]
    updateCron: str = "0 3 * * *"
    waitValue: int = Field(default=24, ge=0, le=43200)
    waitUnit: Literal["hours", "minutes"] = "hours"
    auto: List[str] = Field(default_factory=list)
from backend.store import normalize_policy

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title='Docker Monitor local preview')
EXAMPLES = [
    ('AdGuard', 'adguard/adguardhome:latest', 'shield', 'current', 'running', .4, 76),
    ('CloudDrive2', 'cloudnas/clouddrive2:latest', 'cloud', 'available', 'running', 2.3, 128),
    ('DockerMonitor', 'docker-monitor:local', 'container', 'unchecked', 'running', .6, 82),
    ('HaBridge', 'habridge/ha-bridge:latest', 'house', 'available', 'running', 3.7, 364),
    ('HomeAssistant', 'ghcr.io/home-assistant/home-assistant:stable', 'house', 'current', 'running', 4.2, 864),
    ('Immich', 'ghcr.io/immich-app/immich-server:release', 'images', 'current', 'running', 1.8, 642),
    ('Jellyfin', 'jellyfin/jellyfin:latest', 'film', 'current', 'running', .5, 286),
    ('Nginx', 'nginx:alpine', 'network', 'current', 'running', .2, 24),
    ('Postgres', 'postgres:16-alpine', 'database', 'error', 'warning', 1.1, 312),
    ('Redis', 'redis:7-alpine', 'layers', 'current', 'stopped', 0, 0),
    ('Syncthing', 'syncthing/syncthing:latest', 'folder-sync', 'current', 'running', .7, 138),
    ('UptimeKuma', 'louislam/uptime-kuma:1', 'activity', 'current', 'running', .3, 148),
]
containers = [dict(id=n, name=n, image=image, icon=icon, update=update, state=state,
                   cpu=cpu, memory=mem, metricsAvailable=True, time='12 天',
                   protected=n == 'DockerMonitor', self=n == 'DockerMonitor', auto=False, busy=False,
                   updateError='演示：镜像仓库暂时无法访问' if update == 'error' else '')
              for n, image, icon, update, state, cpu, mem in EXAMPLES]
policy = dict(checkCron='0 */6 * * *', updateCron='0 3 * * *', updateMode='scheduled', waitValue=24, waitUnit='hours', auto=[])
jobs = []
last_check = time.time()
checking = False


notify_settings = dict(enabled=False, configured=False, server='', siteUrl='', iconUrl='', onAvailable=True, onSuccess=True, onFailure=True)


@app.get('/api/notifications')
def notifications():
    return notify_settings


@app.put('/api/notifications')
def save_notifications(value: dict):
    from backend.notifications import endpoint_parts, public_url
    try:
        endpoint = value.pop('endpoint', '')
        if endpoint:
            server, _ = endpoint_parts(endpoint)
            notify_settings.update(configured=True, server=server.split('/')[2])
        if value.get('enabled') and not notify_settings['configured']:
            raise ValueError('请先填写 Bark 推送地址')
        public_url(value.get('siteUrl', ''), '程序地址')
        public_url(value.get('iconUrl', ''), '图标地址')
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    notify_settings.update(value)
    return notify_settings


@app.post('/api/notifications/test')
def test_notifications(value: dict):
    return {'message': '本地演示：已模拟发送测试通知，未连接真实 Bark 服务'}


@app.get('/icon-options')
def icon_options():
    from tools.icon_gallery import page
    return HTMLResponse(page())


@app.get('/api/auth/session')
def session():
    return {'enabled': False, 'authenticated': True}


@app.get('/api/snapshot')
def snapshot():
    return dict(demo=True, containers=copy.deepcopy(containers),
                host=dict(name='Home NAS', version='24.0.2', cpus=4, memory=8589934592),
                policy=copy.deepcopy(policy), lastCheck=last_check,
                jobs=copy.deepcopy(jobs[-30:]), checking=checking, sampledAt=time.time())


@app.put('/api/policy')
def save_policy(value: Policy):
    global policy
    policy = normalize_policy(value.dict(exclude_none=True))
    selected = set(policy.get('auto', []))
    for c in containers:
        c['auto'] = c['name'] in selected and not c['protected']
    return {'saved': True}


def find(container_id):
    c = next((c for c in containers if c['id'] == container_id), None)
    if not c:
        raise HTTPException(404, '演示容器不存在')
    return c


async def perform(job, action, selected):
    global checking, last_check
    job['status'] = 'running'
    try:
        await asyncio.sleep(.8)
        for c in selected:
            if action in ('check', 'check-all'):
                if c['update'] != 'error':
                    c['update'] = 'available' if c['name'] in ('CloudDrive2', 'HaBridge') and c.get('lastResult') != '更新成功' else 'current'
            elif action in ('update', 'update-all'):
                c['update'] = 'current'
                c['lastResult'] = '更新成功'
                c['time'] = '刚刚'
            elif action == 'stop':
                c['state'] = 'stopped'
                c['time'] = '刚刚停止'
            else:
                c['state'] = 'running'
                c['time'] = '刚刚'
            c['busy'] = False
        if action in ('check', 'check-all'):
            last_check = time.time()
        labels = {'check':'检查更新', 'check-all':'检查更新', 'update':'更新', 'update-all':'批量更新', 'start':'启动', 'stop':'停止', 'restart':'重启'}
        job.update(status='success', message='已模拟' + labels[action] + (' · ' + selected[0]['name'] if len(selected) == 1 else ' · ' + str(len(selected)) + ' 个容器'))
    finally:
        checking = False
        for c in selected:
            c['busy'] = False


def submit(background, action, selected):
    global checking
    if any(c['busy'] for c in selected):
        raise HTTPException(409, '模拟任务正在执行')
    job = dict(id=uuid.uuid4().hex, status='queued', message='模拟任务已提交')
    jobs.append(job)
    for c in selected:
        c['busy'] = True
    if action == 'check-all':
        checking = True
    background.add_task(perform, job, action, selected)
    return {'jobId': job['id']}


@app.post('/api/containers/{container_id}/actions')
def action(container_id: str, value: dict, background: BackgroundTasks):
    c = find(container_id)
    action = value.get('action')
    if action not in ('check', 'update', 'start', 'stop', 'restart'):
        raise HTTPException(422, '模拟操作无效')
    if c['protected']:
        raise HTTPException(409, '演示保护容器禁止操作')
    return submit(background, action, [c])


@app.post('/api/check-updates')
def check(background: BackgroundTasks):
    return submit(background, 'check-all', [c for c in containers if not c['protected']])


@app.post('/api/update-all')
def update_all(value: dict, background: BackgroundTasks):
    selected = [find(i) for i in dict.fromkeys(value.get('ids', []))]
    if not selected or any(c['protected'] or c['state'] != 'running' or c['update'] != 'available' for c in selected):
        raise HTTPException(409, '请重新选择可更新的演示容器')
    return submit(background, 'update-all', selected)


@app.get('/login')
def login():
    return FileResponse(ROOT / 'web/login.html')


@app.get('/healthz')
def health():
    return {'status': 'ok', 'demo': True}


from backend.icons import install_routes
install_routes(app)
app.mount('/', StaticFiles(directory=ROOT / 'web', html=True))
