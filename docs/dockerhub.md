# Docker Monitor

Docker 容器监控与镜像更新工具，支持卡片/列表、浅色/暗色主题、登录保护、手动与定时更新、健康检查和失败回退、Bark 通知。

源码与问题反馈：[GitHub](https://github.com/weidaye1122/DockerMonitor)。

## 安装


镜像：[weidaye1122/docker_monitor](https://hub.docker.com/r/weidaye1122/docker_monitor)。支持 `linux/amd64`（常见 x86 群晖、PC）和 `linux/arm64`（ARM64 主机）。`latest` 跟随最新正式版本，固定版本可使用 `1.0.2`。版本改动见 [更新日志](https://github.com/weidaye1122/DockerMonitor/blob/main/CHANGELOG.md)。

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

固定版本安装将镜像改为 `weidaye1122/docker_monitor:1.0.2`。仅允许本机访问时，将端口改为 `"127.0.0.1:9090:8080"`。


## 使用说明

- 默认关闭所有容器的自动更新，可在网页中逐个启用。
- 更新保留已有卷、环境变量、端口和网络；通过启动/健康检查后清理旧容器，失败时尝试恢复原容器。升级前仍建议备份重要应用数据。
- 支持本机普通 Docker 和 Compose 容器；不管理 Swarm 服务，不重写其他项目的 Compose 文件。
- 不自动从固定版本标签跨越到更高版本。
- 本程序及带 `docker-monitor.protected=true` 标签的容器受到保护。
- 通知支持 Bark，默认关闭；手机需能访问配置的程序地址才能显示默认图标。

完整功能和限制见 [项目 README](https://github.com/weidaye1122/DockerMonitor#readme)。

## 反向代理与穿透隧道

通过 Lucky、Nginx/NPM、群晖反代和穿透隧道访问，无需配置外部域名即可登录。支持 HTTPS、非标准端口、IPv6、改写 Host 和丢失代理头的隧道，同时保留局域网 HTTP 直连。旧版的 `PUBLIC_URL` 可删除。

页面自动获取并提交 Cookie、会话绑定的签名 CSRF Token，保护登录、退出和所有写操作。HTTPS 浏览器访问即使经过 HTTP 后端，也使用 Secure Cookie。令牌失效时仅重试明确未执行的请求一次；登录过期需重新登录。代理需转发整个站点，并保留 Cookie 和自定义请求头。

`TRUSTED_PROXY_IPS` 是可选配置，仅用于识别可信代理头和真实客户端 IP，以区分登录限流。默认只信任回环地址；没有可信客户端地址时按代理连接地址共享限流。当前支持域名根路径部署，不支持 `/docker-monitor/` 等子路径。

完整配置见 [反代与隧道说明](https://github.com/weidaye1122/DockerMonitor#https-反代与穿透隧道)。
