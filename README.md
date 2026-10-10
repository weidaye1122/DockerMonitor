# Docker Monitor

沿用已确认的列表/卡片界面，连接本机 Docker，提供启动、停止、重启、镜像检查和更新。支持浅色、暗色、跟随系统。

## Docker 安装

镜像：[weidaye1122/docker_monitor](https://hub.docker.com/r/weidaye1122/docker_monitor)。支持 `linux/amd64`（常见 x86 群晖、PC）和 `linux/arm64`（ARM64 主机）。`latest` 跟随最新正式版本，固定版本可使用 `1.0.1`。版本改动见 [更新日志](CHANGELOG.md)。

### Compose 安装（推荐）

新建目录，将以下内容保存为 `compose.yaml`。将账号密码换成自己的值：

```yaml
services:
  docker-monitor:
    image: weidaye1122/docker_monitor:latest
    container_name: docker-monitor
    restart: unless-stopped
    labels:
      docker-monitor.protected: "true"
    ports:
      - "9090:8080"
    environment:
      TZ: Asia/Shanghai
      ADMIN_USERNAME: "admin"
      ADMIN_PASSWORD: "请替换为自己的强密码"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./data:/data
```

在该目录执行：

```sh
mkdir -p data
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=50
```

打开 `http://主机IP:9090`，使用配置的账号密码登录。群晖可在 Container Manager 中创建项目并粘贴以上 YAML；建议项目目录为 `/volume1/docker/DockerMonitor`。若 9090 已占用，可改为 `"19090:8080"` 并访问 19090 端口。`./data` 保存策略、任务及通知配置，升级时保留该目录。Docker socket 挂载让程序能够管理主机容器，请只向信任的网络和使用者开放。

升级时在原目录执行：

```sh
docker compose pull
docker compose up -d
```

固定版本安装将镜像改为 `weidaye1122/docker_monitor:1.0.1`。仅允许本机访问时，将端口改为 `"127.0.0.1:9090:8080"`。

### 使用仓库内的 Compose

仓库内的 `compose.yaml` 默认只绑定本机回环地址。在项目目录执行：

```sh
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=50
```

打开 <http://127.0.0.1:9090>。当前配置只绑定本机回环地址。要从 NAS 局域网访问，在 `.env` 设置：

```dotenv
MONITOR_BIND=0.0.0.0
MONITOR_PORT=9090
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请替换为自己的密码
```

重新执行 `docker compose up -d`。账号与密码分别由 `ADMIN_USERNAME`、`ADMIN_PASSWORD` 指定，账号默认为 `admin`。配置密码后，未登录访问会进入独立登录页，支持浅色、暗色和跟随系统。登录会话有效期为 12 小时，退出立即失效；服务重启后需要重新登录。密码留空时保留本机开发访问方式。Docker socket 挂载让这个服务能够操作主机上的容器，应只向信任的使用者开放。

### Docker run 安装

也可直接拉取镜像运行，将账号密码换成自己的值：

```sh
mkdir -p /opt/docker-monitor/data
docker pull weidaye1122/docker_monitor:latest
docker run -d --name docker-monitor --restart unless-stopped \
  --label docker-monitor.protected=true \
  -p 9090:8080 \
  -e TZ=Asia/Shanghai \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD='请替换为自己的强密码' \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt/docker-monitor/data:/data \
  weidaye1122/docker_monitor:latest
docker logs --tail=50 docker-monitor
curl -fsS http://127.0.0.1:9090/healthz
```

### 从源码构建

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

上述命令使用本地 `docker-monitor:local` 镜像。已有 Docker Desktop / Linux ARM64 容器和 x86 群晖 Compose 部署验证记录见下文。

### HTTPS 反代与穿透隧道

支持通过 Lucky、Nginx/Nginx Proxy Manager、群晖反向代理，以及 FRP、Cloudflare Tunnel 等隧道访问。后端仍使用 HTTP；浏览器来源校验和登录 Cookie 会按可信代理传来的外部协议、域名和端口处理，支持非标准端口和 IPv6。代理应保留外部 `Host`（包含端口），传递 `X-Forwarded-Proto`；若改写 Host，应另外传递 `X-Forwarded-Host`，端口也可由 `X-Forwarded-Port` 提供。显式 Host 端口优先于单独的端口头。

若隧道没有传递这些头，或外部地址与后端地址不同，可在 Compose 的 `environment` 中明确指定外部地址。例如 Lucky 的访问地址为 `https://dm.543221.xyz:6/`：

```yaml
      PUBLIC_URL: "https://dm.543221.xyz:6"
```

使用仓库内的 Compose 时，也可在 `.env` 添加 `PUBLIC_URL=https://dm.543221.xyz:6`。Docker run 安装则增加 `-e PUBLIC_URL=https://dm.543221.xyz:6`。修改后重新创建容器。这个配置只额外允许该外部来源；局域网 HTTP 直连仍可登录。隧道需转发整个站点，包括 `/api/` 和静态资源，且不能删除请求中的 `Origin`、`X-Monitor-Request` 或 Cookie。

默认只信任回环地址发来的代理头。Docker、NAS 或远程代理可用 `TRUSTED_PROXY_IPS` 明确指定后端实际看到的代理连接 IP，多个 IP 或 CIDR 用逗号分隔，例如：

```yaml
      TRUSTED_PROXY_IPS: "127.0.0.1,::1,172.18.0.1"
```

代理必须覆盖来自客户端的协议、主机和端口头；后端端口应仅供代理或信任的使用者访问。私网地址不自动被视为代理，因为 Docker 端口映射也可能把普通客户端显示为网桥 IP。固定外部地址场景只需配置 `PUBLIC_URL` 即可修复来源校验，无需放开代理头信任。多层代理应由最终转发到后端的一层提供正确的外部地址；可信 `X-Forwarded-For` 会按代理链解析，以区分不同客户端的登录限流。没有可信真实客户端地址时，登录限流仍按代理或隧道连接地址共享。

HTTPS 在 Nginx 这一层终止时，可使用以下配置（上游端口按实际修改）：

```nginx
location / {
    proxy_pass http://127.0.0.1:9090;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

容器启动命令已设置 `--no-proxy-headers`，由应用统一解析代理头。自定义 Uvicorn 启动命令也应保留此参数，避免重复解析丢失原始代理 IP。当前部署在域名根路径，例如 `https://monitor.example.com/`；不支持直接挂到 `/docker-monitor/` 等子路径。静态资源、API、PWA 图标和通知默认地址均使用同源根路径。

## 群晖部署

本次部署使用本地导入的 `docker-monitor:nas-amd64-ui` 镜像，项目目录为 `/volume1/docker/DockerMonitor`，数据保存在该目录的 `data` 子目录。因 NAS 的 9090 端口已有服务，本次使用 <http://10.0.0.6:19090/>，登录用户名由使用者自定义，密码由使用者自行填写。在 Compose 的 `environment` 中可同时指定账号和密码：

```yaml
      ADMIN_USERNAME: "自己的账号"
      ADMIN_PASSWORD: "自己的密码"
```

改完仅保存 YAML，再手动运行 `Docker Monitor Deploy` 任务，使 Compose 重新创建容器。账号密码没有在网页中提供修改入口。

本次 DSM 界面导入后曾显示新版镜像，但部署时本机 Docker 引擎找不到该标签；界面导入的镜像未持续保留，原因尚未确认。最终改为将镜像归档上传到项目目录，用同一 Docker 命令直接加载、检查后再部署，已成功。使用本地镜像时，在项目目录执行：

```sh
docker load -i docker-monitor-amd64-ui.tar.gz
docker image inspect docker-monitor:nas-amd64-ui --format '{{.Id}} {{.Architecture}}'
docker compose -p docker-monitor -f compose.yaml up -d --pull never --no-deps docker-monitor
```

未开启 SSH 时，可以通过 DSM 任务计划的 root 用户定义脚本手动执行。已创建的 `Docker Monitor Deploy` 任务关闭自动计划，只供手动部署使用，先加载上述归档并核对镜像，再执行 Compose；输出写入项目目录的 `deploy.log`。任务末尾另含幂等的历史镜像引用修复命令，输出到 `repair.log`，只处理 CloudDrive2、HaBridge、SaveanyBot；正常引用会跳过。容器使用 `unless-stopped` 重启策略，不依赖该任务定时启动。后续部署前，应将最终镜像归档上传覆盖同名文件。

本次已确认 Container Manager 项目中存在 `DockerMonitor` 容器，`/healthz` 返回 HTTP 200，未登录首页返回 HTTP 303 并跳转 `/login`，未登录访问 `/api/snapshot` 返回 HTTP 401，且不再返回 Basic 登录弹窗标识。独立登录页已部署；本地浏览器已验证自定义账号登录、错误密码提示、浅色与暗色外观、退出登录。NAS 已验证容器数据显示、名称排序，以及刷新后的首次数据请求约 70–91 毫秒（主题调整前）。

2026-10-09 15:20 新版已部署；15:25 手动维护任务再次核对镜像，NAS 实际运行 `sha256:2af99463db52ff4c4ab382123a1e1523075959454049ac78537c6a2df37280de`（amd64），状态 healthy。健康接口、登录跳转、通知 API 的未登录保护、版本 a2 图标及 manifest 验证通过。用户登录后，实际页面显示 36 个容器（35 个运行、1 个停止），默认卡片、自然名称排序、A 图标和通知设置正常，浏览器无错误或警告。V2RayA 检查已恢复，显示有更新；本次未更新它。

15:26 三个旧版更新容器 CloudDrive2、HaBridge、SaveanyBot 的镜像引用修复完成。维护脚本逐个验证原镜像 ID、环境配置、端口、挂载、网络和运行状态保持一致；修复成功后自动删除原容器。群晖 Container Manager 和 DockerCopilot 均已显示正常仓库标签，不再显示 sha256 引用，也没有残留 backup 容器。仍为 36 个容器；Go2RTC 保持原来的停止状态。

## 本次上线改动

独立镜像查询、Bark 通知、A 图标、成功更新后清理旧容器，以及镜像标签兼容修复均已上线。

- 公共 Docker Hub / GHCR 镜像优先匿名直连原仓库；失败时使用 Docker 引擎已有凭据，再尝试已配置的 Docker Hub 备用源。自定义和私有仓库仍由 SDK 查询，保留已有授权与 TLS 配置。短时缓存复用同一轮相同镜像的查询，手动与新一轮定时检查会刷新结果。
- 备用源默认只读取引擎配置的 HTTPS `RegistryConfig.Mirrors`，可用环境变量 `REGISTRY_MIRRORS` 指定逗号分隔的 HTTPS 源（最多 4 个）；没有硬编码第三方公共加速源。查询清单校验 SHA-256，实际拉取固定相同摘要；原拉取失败后尝试官方显式地址及已配置备用源。所有拉取完成后才进入容器替换流程，失败时原容器继续运行，不改写主机镜像源和原镜像跟踪地址。
- 镜像检查错误使用统一提示，不显示仓库附带的联系信息或自定义长文案。NAS 已成功检查 V2RayA，原有错误消失并显示有更新；备用源故障切换未进行现场强制验证。
- 本程序卡片显示“本程序 · 已保护”，其他受保护容器显示“受保护 · 不参与更新”，与普通卡片的状态区一致。
- 顶部“通知”提供 Bark 设置，支持启用、三类事件、测试推送及最近发送结果。完整推送地址包含设备 Key；保存后 API 不再返回 Key，留空保留原值。默认关闭，同一设备同一容器的已发现摘要只通知一次；真正更新成功或失败时单独通知，网络推送在独立队列执行。
- 通知默认用程序访问地址下的 `/icons/icon-192.png?v=a2`，也可指定其他图标 URL。手机必须能访问该地址；内网地址仅在手机可达 NAS 的网络中显示图标，建议使用自己的 HTTPS 公网程序地址。推送按 [Bark 官方 API](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md) 使用 POST JSON，Key 放在请求体，不拼入推送请求路径。未配置真实设备，手机到达及图标显示尚未实测。
- 用户选定 A“容器 · 监测”，已接入顶部标识、本程序卡片、登录页、网页标签/书签、Apple 主屏幕、PWA 以及 Bark 默认图标。SVG、PNG 和 ICO 由 `backend/icons.py` 在内存绘制并缓存，通过公共图标路由提供；未额外导出图片文件。资源版本 `a2` 避免旧图标缓存。`tools/build_icons.py` 默认仅在内存验证，只有显式 `--export` 才写入图标文件。
- 本地验证：69 项单元测试通过；真实 Docker 临时容器验证启动/停止/重启、本程序保护、同标签摘要更新及配置和数据保留、成功后删除旧容器并保留原始镜像标签、健康检查失败回滚、零分钟延迟更新和实际 Cron 定时更新均通过。连续手动和自动更新后不残留旧容器；临时测试容器已清理，测试策略已恢复。旧版成功备份迁移清理通过单元测试；NAS 已完成上述三个容器的同镜像引用修复与清理。

## 已接入

- Docker 容器名称、运行状态先显示，CPU 和内存后台采样，每 5 秒刷新，不阻塞页面加载。尚未取得或超过 20 秒的资源采样显示为不可用；顶部保留容器总数、运行数、CPU 和内存四项概览。
- 列表、卡片均按名称自然排序，忽略大小写，数字按数值顺序排列。
- 浅色、暗色与跟随系统主题，默认卡片布局；有更新的容器使用柔和琥珀色标签及更新按钮提示，卡片外框与普通卡片一致，顶部显示可更新数量。操作提示与当前主题一致，显示在页面顶部下方。
- 容器操作和后台刷新就地更新界面，保留页面滚动位置；卡片按键、分段切换及弹窗提供轻量交互反馈。
- 一键更新当前已发现新版且正常运行的容器，确认清单后逐个执行；运行状态改变、受保护或任务冲突时拒绝提交，执行前再次检查，单个失败不阻断后续容器。
- 更新策略弹窗顶部保存/取消，双列容器列表独立使用原生滚动条，打开弹窗锁定背景页面；等待时间可选分钟或小时，旧小时策略自动兼容。
- 浏览器标签/书签 ICO、SVG 图标，Apple 主屏幕图标以及 PWA manifest（192/512 和 maskable 图标）。内网 HTTP 可显示网页图标；浏览器完整 PWA 安装一般要求 HTTPS。
- 启动、停止、重启，确认框之后发送真实任务；同一容器同时只允许一个操作。
- 镜像仓库摘要比较，不通过“标签字符串变化”判定更新，不在检查时提前拉取镜像。
- 手动更新当前标签对应的镜像；不自动从固定版本标签跨越到更高版本。
- 保留环境变量、命令、端口、重启策略、已有卷、绑定挂载、自定义网络、别名和显式静态 IP；匿名卷复用原卷。
- 拉取确定的目标摘要，保留原始仓库标签用于新容器的 `Config.Image`，兼容其他 Docker 管理工具；创建后、启动前核对实际镜像 ID 必须匹配已拉取目标，否则恢复原容器。拉取期间如果原容器配置或运行状态被外部修改，取消更新。该兼容修复已部署，并在上述三个 NAS 容器完成验证。
- 新容器有 HEALTHCHECK 时等待健康；失败、退出或超时尝试恢复原容器及网络。无 HEALTHCHECK 时检查持续运行至少 10 秒。
- SQLite 保存更新策略、首次发现时间、任务状态和恢复计划；服务重启时恢复未完成的更新。
- 镜像仓库临时请求失败不会清空首次发现时间；拉取期间关闭自动更新，会取消本次自动重建。
- 当前监控容器及 `docker-monitor.protected=true` 标签容器受保护，回退副本也不可通过本服务启动。

页面不显示容器大小，也不执行文件系统大小扫描。CPU 使用率沿用 Docker 多核口径，可以大于 100%；内存扣除 inactive file 缓存。

## 自动更新

默认所有容器关闭自动更新。检测与更新是两个独立步骤，时区固定为 `Asia/Shanghai`，Cron 使用五位格式 `分 时 日 月 周`。

| 设置 | 规则 |
|---|---|
| 检测频率 | 按 Cron 检查镜像仓库，例如 `0 */6 * * *` 每 6 小时 |
| 按固定时间更新 | 已检测到更新时，等下一个更新 Cron 时点执行，不使用等待时长 |
| 发现后延迟更新 | 从首次发现该目标摘要开始计时，到时执行，不受更新 Cron 限制；支持分钟或小时，0 表示立即 |

同一摘要重复检查不会重新计时；目标摘要变了，等待期重新开始。停止、健康异常、重启中、受保护以及已有任务的容器跳过。更新失败后至少间隔 15 分钟再自动尝试，避免反复重启。

程序在更新期间暂时把原容器改名为 `<原名称>.backup-<任务编号>` 并停止；新容器通过启动/健康检查后，自动删除旧容器，不删除数据卷，也不清理镜像。验证失败时仍恢复原容器。健康验证通过的状态先持久化，服务中断后继续清理，不误回滚到已删除的容器；清理失败会保留提示并每分钟重试。新版启动时也会清理旧版成功任务记录中的备份：核对新容器的 ID、名称、镜像、任务归属和运行状态，以及旧容器的 ID、备份名称和停止状态，全部匹配才删除；不会按名称后缀批量删除。此改动已部署，NAS 旧版成功任务备份及本次修复备份已清理。容器回退不会撤销应用对卷内数据进行的数据库迁移。

## 支持范围

- 支持单个普通 Docker 容器及普通 Compose 容器的原位更新，保留 Compose 标签和配置；不会重写 Compose YAML，也不执行整个项目的依赖更新。
- `network_mode: container:...` / `pid: container:...`、有共享命名空间依赖、`links`、`volumes_from` 和 `AutoRemove` 容器会拒绝原位更新，应使用原部署项目更新。
- Swarm 服务应使用 Swarm 原生更新，不在本服务管理范围内。
- 固定 `@sha256:`、无仓库记录的本地镜像不支持跟踪标签更新。Docker containerd 存储会使用容器的 manifest/index 描述信息，兼容标签移动后旧镜像仓库信息消失的情况。
- 公共仓库可直接检查。私有仓库通过 Docker SDK 的标准 Docker 登录配置读取凭据：需要时挂载自己的 Docker `config.json` 到 `/root/.docker/config.json:ro`。Desktop credential helper 不能在 Linux 容器内直接使用，应提供 Linux 容器可读取的登录配置。私有仓库实测尚未覆盖。
- 目前是本机单 Docker 引擎；已接入 Bark 通知；未接入多主机、日志浏览或通用删除容器操作。

## 参考项目及实现选择

| 项目 | 使用的设计参考 |
|---|---|
| [WUD](https://github.com/getwud/wud) | 检测与触发分离、健康检查、保留原容器和失败回退 |
| [Dockpeek](https://github.com/dockpeek/dockpeek) | 更新时保留容器配置及网络 |
| [Arcane](https://github.com/getarcaneapp/arcane) | 目标镜像固定、任务持久化和中断恢复 |
| [Dockwatch](https://github.com/Notifiarr/dockwatch) | 检测计划与逐容器自动更新开关 |

这里参考流程与边界，未复制这些项目源代码，也未把它们作为隐藏的后台服务。实际 Docker 操作用 [Docker SDK for Python](https://github.com/docker/docker-py)，服务使用 FastAPI，服务端 Cron 用 croniter，界面表达式预览用 Croner；最终校验与调度以服务端为准。

## 开发与验证

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
# Linux 默认 /var/run/docker.sock；Docker Desktop 设置成其当前 context 的 socket。
DOCKER_HOST=unix:///var/run/docker.sock .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 9090 --no-proxy-headers
```

本地交互预览（使用内存中的模拟容器，不连接 Docker，不操作群晖）：

```sh
.venv/bin/python tools/build_web.py
.venv/bin/uvicorn tools.preview:app --host 127.0.0.1 --port 9091 --no-access-log
```

打开 `http://127.0.0.1:9091/`，可模拟检查、启停、重启、一键更新和保存策略。模拟状态仅在本次进程内保留，重启预览会恢复初始状态。登录页视觉预览在 `/login`。

前端源码是 `web/app.js`、`web/app.css`、`web/index.html`。原型留在 `design/prototype.html`；迁移脚本 `python3 tools/build_web.py` 可从原型重新生成前端，会覆盖生成文件。原先的 `web/preview.html` 只用于历史 UI 演示，Docker 镜像不包含它。

真实 Docker 集成测试会创建唯一标签的临时容器、网络、匿名卷和本地仓库，使用端口 5001、5002，然后恢复原策略并清理自己的测试资源：

```sh
docker pull registry:2
docker pull busybox:1.37
DOCKER_HOST=unix:///var/run/docker.sock .venv/bin/python tools/integration_test.py
```

测试涵盖真实启停重启、受保护操作拒绝、同标签更新、配置/数据保留、健康失败回退、延迟更新和实际 Cron 时点更新。结果写入 `output/integration/results.json`。

本次验证：69 项单元测试通过。最终 amd64 镜像在真实 Docker 集成流程通过启停重启、自身保护、历史 SHA 镜像引用修复、同标签更新、配置和卷数据保留、成功删除旧容器、健康失败回退、延迟更新和实际 Cron 定时更新；临时测试资源已清理。x86 群晖已部署同一构建，登录后的容器页和通知设置已检查，三个历史引用异常容器已修复并在群晖及 DockerCopilot 验证。服务中断恢复已通过单元测试，尚未覆盖真实进程强制退出；私有仓库授权和真实 Bark 手机送达尚未实测。
