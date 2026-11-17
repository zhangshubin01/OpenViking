# OrbStack 本机部署

本目录是 macOS OrbStack 上跑 OpenViking 的本地部署文件，配合仓库根目录的
`docker-compose.orbstack.yml` 使用。

| 文件 | 作用 |
| --- | --- |
| `docker-compose.orbstack.yml` | 单容器编排：发布 `1933`、挂载 `~/.openviking`、复用外部网络 `openviking-shared` |
| `docker/openviking-orbstack-entrypoint.sh` | 容器入口：启动 `openviking-server --with-bot`，并把 `0.0.0.0:1933` 转发到服务真实监听 |
| `docker/openviking-orbstack-forward.py` | 上面那层 TCP 转发（纯标准库；源码构建的镜像里没有 socat） |
| `docker/ov.conf.example` | 去密钥的 `ov.conf` 模板，真实配置放在宿主 `~/.openviking/ov.conf` |

## 首次准备

```bash
# 1. 外部网络（只需一次）
docker network create openviking-shared

# 2. 配置：从模板复制，替换所有 <...> 占位符，并收紧权限
mkdir -p ~/.openviking
cp docker/ov.conf.example ~/.openviking/ov.conf
chmod 600 ~/.openviking/ov.conf
$EDITOR ~/.openviking/ov.conf

# 3. 镜像：本机构建，tag 跟分支与提交走（compose 里的 image 要同步改）
SHA=$(git rev-parse --short=9 HEAD)
docker build --build-arg OPENVIKING_VERSION=<version> -t openviking:<branch>-$SHA -f Dockerfile .

# 4. 启动
docker compose -f docker-compose.orbstack.yml up -d
```

## 不能改的地方

- `ov.conf` 的 `server.host` / `server.port` 必须是 `127.0.0.1` / `1934`：容器内入口脚本占用 `0.0.0.0:1933` 做转发，两者改成同一个端口会互抢。
- compose 里的 image 是本机构建 tag，**不要 `docker compose pull`**，远端没有这个 tag。
- 不要在 Dockerfile 里补 socat 走老路：转发已由 `openviking-orbstack-forward.py` 承担。
- `ov.conf` 与 `ovcli.conf` 都被 `.gitignore` 排除，含密钥，永远不要提交；`ovcli.conf` 用 `ov config add <name> --url http://127.0.0.1:1933 --activate` 在容器内生成。

## 验证

```bash
docker compose -f docker-compose.orbstack.yml ps                  # 期望 healthy
curl -sS http://127.0.0.1:1933/health                             # healthy:true + version + auth_mode
docker exec openviking openviking-server doctor                   # 配置/鉴权/模型自检
curl -sS -X POST http://127.0.0.1:1933/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer <user-key>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
```

`doctor` 的 VLM 项只校验配置、不做真实调用，要确认模型链路得在容器内实调一次。
