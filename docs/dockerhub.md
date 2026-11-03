# Docker Monitor

Docker 容器监控与镜像更新工具，支持卡片/列表、浅色/暗色主题、登录保护、手动与定时更新、健康检查和失败回退、Bark 通知。

源码与问题反馈：[GitHub](https://github.com/weidaye1122/DockerMonitor)。

## 安装

镜像：[weidaye1122/docker_monitor](https://hub.docker.com/r/weidaye1122/docker_monitor)。支持 `linux/amd64`（常见 x86 设备）和 `linux/arm64`（ARM64 主机）。`latest` 跟随最新正式版本，固定版本可使用 `1.0.3`。版本改动见 [更新日志](https://github.com/weidaye1122/DockerMonitor/blob/main/CHANGELOG.md)。

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

打开 `http://主机IP:9090`，使用配置的账号密码登录。若 9090 已占用，可改为 `"19090:8080"` 并访问 19090 端口。`./data` 保存策略、任务及通知配置，升级时保留该目录。Docker socket 挂载让程序能够管理主机容器，请只向信任的网络和使用者开放。

升级前先按 [数据与备份说明](https://github.com/weidaye1122/DockerMonitor#数据与备份) 核对实际挂载、完成备份，并保留旧镜像。在原部署目录、原 Compose 项目中执行：

```sh
docker compose pull docker-monitor
docker compose up -d --no-deps docker-monitor
```

固定版本安装将镜像改为 `weidaye1122/docker_monitor:1.0.3`。仅允许本机访问时，将端口改为 `"127.0.0.1:9090:8080"`。

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

## 使用说明

- 默认关闭所有容器的自动更新，可在网页中逐个启用。
- 更新保留已有卷、环境变量、端口和网络；通过启动/健康检查后清理旧容器，失败时尝试恢复原容器。升级前仍建议备份重要应用数据。
- 支持本机普通 Docker 和 Compose 容器；不管理 Swarm 服务，不重写其他项目的 Compose 文件。
- 不自动从固定版本标签跨越到更高版本。
- 本程序及带 `docker-monitor.protected=true` 标签的容器受到保护。
- 通知支持 Bark，默认关闭；手机需能访问配置的程序地址才能显示默认图标。

完整功能和限制见 [项目 README](https://github.com/weidaye1122/DockerMonitor#readme)。

## 反向代理与穿透隧道

通过 Lucky、Nginx/Nginx Proxy Manager 等反向代理，以及 FRP、Cloudflare Tunnel 等隧道访问时，无需填写 `PUBLIC_URL` 或可信代理地址才能登录。页面自动获取 Cookie 和会话绑定的签名 CSRF Token，登录、退出和所有写操作均提交令牌；不再将浏览器外部地址与后端 HTTP 地址比较。支持 HTTPS、非标准端口、IPv6、改写 Host 以及丢失代理头的隧道，局域网 HTTP 直连仍可使用。旧版添加的 `PUBLIC_URL` 可删除，保留也不影响登录。

浏览器自动传递实际 HTTP/HTTPS 协议信息，仅用于 Cookie 的 Secure 标记，不作为请求授权。HTTPS 外部访问即使经过 HTTP 后端，也会使用 Secure、HttpOnly、SameSite=Strict Cookie。CSRF Token 不缓存，并绑定当前会话；令牌过期或服务重启后，页面只对明确被 CSRF 校验拒绝、尚未执行的操作刷新令牌并重试一次。登录过期仍需重新登录。

代理和隧道需转发整个站点（包括 `/api/` 和静态资源），保留 Cookie 和页面提交的自定义请求头。无需配置 CORS 或放开跨站访问。当前浏览器通过 `Sec-Fetch-Site` 拦截明确跨站操作，较旧浏览器仍由签名 CSRF Token、自定义请求头和浏览器同源读取限制保护。

`TRUSTED_PROXY_IPS` 仅用于可信代理提供的外部地址和真实客户端 IP 识别，不是登录前提。默认只信任回环地址；需要按外部客户端 IP 区分登录限流时，可添加后端实际看到的代理连接 IP，多个 IP 或 CIDR 用逗号分隔：

```yaml
      TRUSTED_PROXY_IPS: "127.0.0.1,::1,172.18.0.1"
```

可信代理应覆盖客户端自带的代理头；后端端口应仅供代理或信任的使用者访问。没有可信真实客户端地址时，登录限流仍按代理或隧道连接地址共享。代理可以传递 `X-Forwarded-Proto`、`X-Forwarded-Host` 和 `X-Forwarded-Port`，但即使缺少这些头也不影响浏览器登录及 HTTPS Cookie。

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

## 维护者发布

这里约定以后的手动发布，不会自动执行。日常开发和整理先保留本地待提交修改，用户要求正式发布新版本时再集中提交、打标签并推送 GitHub；单独明确要求保存 Git 检查点时按指定范围执行。日常可在 `main` 开发，有隔离需要再建分支；不要求 PR、多人审核或 GitHub 自动镜像发布。仅整理文档不升版本。

默认流程：**本地验证完成 → 用户要求正式发布 → AI 递增版本号、整理更新日志 → 提交源码、打标签并推送 GitHub → 一次构建并同时推送固定版本和 `latest`**。功能验证放在发布前的本地阶段，上传后核对发布结果即可。

开发、试用和测试均在本地完成；早期临时测试的环境只保留为历史记录，不作为发布前提。

1. 日常查看 Git 状态和差异，保留本地及其他任务未提交的工作，不在每次修改完成后自动暂存、提交或推送。先本地构建 `docker-monitor:local`，按改动范围完成必要检查和隔离验证；涉及容器更新、数据保留或恢复时才增加对应测试。普通本地构建用 `dev`，记录未提交差异，不消耗正式版本号。
2. 需要发布时，由 AI 核对 Git、CHANGELOG 和 Docker Hub 已有版本，负责选取未使用的新版本；默认递增修订号（如 `1.0.3` → `1.0.4`），用户另有指定时按要求处理。把 CHANGELOG 相应“未发布”条目整理为 `X.Y.Z — 日期`，同步文档中的当前版本引用，保留无关待发布条目，不覆盖已发布固定版本。
3. 审查本次版本累积的待发布改动，连同版本说明一起提交，给确切提交打 `vX.Y.Z` 标签并推送 GitHub。保留无关或未完成的本地工作，必要时从发布提交建立干净工作目录构建。正式构建来自干净、已提交的源码；CHANGELOG 版本、Git 标签、镜像固定标签保持一致，Dockerfile 的 `VERSION=X.Y.Z`、`REVISION=完整提交号` 写入已有 OCI 标签。当前已有 `v1.0.0`、`v1.0.1`、`v1.0.2`、`v1.0.3`，不重打或移动这些标签。发布准备期间如果又改了功能代码，先完成相应本地检查。

下面的 `1.0.4` **仅为下次版本示例，实际发布前仍需核对已有标签**。本地验证完成后，核对已登录的仓库和可用的本地 buildx 构建器；Docker 登录沿用本机配置，不把令牌放文档、命令参数或构建参数。发布不改变现用容器或部署设置。以下变量在同一 shell 中使用：

```sh
release_version=1.0.4
image_repo=weidaye1122/docker_monitor
source_revision="$(git rev-parse HEAD)"
release_dir="../本地资料/验证/发布/$release_version"
mkdir -p "$release_dir"
git rev-parse HEAD > "$release_dir/revision.txt"
```

同一条命令构建 amd64、arm64，并同时推送新固定版本和 `latest`，两个标签使用同一份构建产物：

```sh
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg VERSION="$release_version" \
  --build-arg REVISION="$source_revision" \
  --metadata-file "$release_dir/build.json" \
  -t "$image_repo:$release_version" -t "$image_repo:latest" --push .
docker buildx imagetools inspect "$image_repo:$release_version"
docker buildx imagetools inspect "$image_repo:latest"
docker buildx imagetools inspect --raw "$image_repo:$release_version" > "$release_dir/manifest.json"
```

上传后核对固定版本与 `latest` 的顶层摘要一致，清单同时有 `linux/amd64` 和 `linux/arm64`，构建记录中的源码版本/提交与本次发布相符。构建证明可能额外显示 `unknown/unknown`，不能替代任一运行架构。这是发布结果核对，功能验收已在本地完成，无需另等一轮验收再更新 `latest`。

保留构建记录、源码提交号、固定标签、摘要、实际本地检查和未验证项在 `../本地资料/验证/发布/版本/`，镜像归档放 `../本地资料/镜像归档/`，交付时说明。仿真与实机试用分别记录，构建成功不等于两种架构都已实机运行验证。构建或上传异常时如实记录标签状态，不宣称发布完成。CHANGELOG 按版本记录用户可见变化；源码标签和 OCI 标签把版本对应到确切代码。保留旧固定版本作为回退，不在清理中删除。

镜像发布到此结束。更新正在运行的容器是另一项操作，要按 [README 的备份、更新和恢复方法](https://github.com/weidaye1122/DockerMonitor#数据与备份) 核对原项目、挂载和回退版本，不随发布自动重建容器。

## 真实 Docker 验证

日常 UI 用 `tools.preview:app` 的内存模拟；代码基本测试用 pytest。只有涉及真实容器更新、配置/数据保留或恢复时才运行 `tools/integration_test.py`，不因小文档修改重复部署。

现有脚本的边界必须先核对：

- 默认服务 URL 是 `http://127.0.0.1:9090`，可用 `MONITOR_TEST_URL` 指定，但脚本会读写目标服务的全局策略，并按固定名称查找 `docker-monitor` 测试自身保护。不能将它指向现用服务，也不能拿现有同名容器验证保护。
- 只在专门创建的测试 Docker 引擎、全新数据和专门启动的测试服务上执行；测试服务内部可沿用脚本要求的名称和端口，不修改现有部署。客户端和测试服务必须指向同一个测试引擎，测试端口 5001、5002 不得与现用服务冲突。
- Docker SDK 从 `DOCKER_HOST` 等环境变量取连接，不自动跟随 Docker CLI 的当前 context。只切换 CLI context 不能证明脚本隔离，必须核对双方实际连接和服务的数据挂载。
- 脚本创建 `docker-monitor.test-run=<本次标记>` 标签的临时容器、网络和匿名卷，覆盖启停重启、同标签更新、配置和数据保留、健康失败回退、延迟及 Cron 更新。测试只触碰这些专门创建的资源；保留原有保护、配置保留与失败回退行为。
- 脚本继续将报告写入 `output/integration/results.json`，下次运行会覆盖；需保存的证据先另存到 `../本地资料/验证/日期-本次标记/`，不为目录对齐改写脚本输出。
- `finally` 先恢复策略再清理；如果恢复请求失败，后续清理可能没有执行。测试后按本次标签、精确 ID、挂载及引用逐项核对。脚本不删除测试镜像和构建缓存，另按 README 的清理规则处理，不能用全局清理补救，也不删预先存在的基础镜像。

没有可确认的本地专用测试环境时跳过真实测试，并明确说明未验证范围。早期临时测试和集成验证证据见 [使用细节与历史部署记录](https://github.com/weidaye1122/DockerMonitor/blob/main/docs/operations.md)，不作为当前测试目标或当前版本的验收结果。
