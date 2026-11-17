# OrbStack 本机部署

本目录是 macOS OrbStack 上跑 OpenViking 的本地部署文件，配合仓库根目录的
`docker-compose.orbstack.yml` 使用。

| 文件 | 作用 |
| --- | --- |
| `docker-compose.orbstack.yml` | 单容器编排：发布 `1933`、挂载 `~/.openviking`、复用外部网络 `openviking-shared` |
| `docker/openviking-orbstack-entrypoint.sh` | 容器入口：启动 `openviking-server --with-bot`，并把 `0.0.0.0:1933` 转发到服务真实监听 |
| `docker/openviking-orbstack-forward.py` | 上面那层 TCP 转发（纯标准库；源码构建的镜像里没有 socat） |
| `docker/ov.conf.example` | 去密钥的 `ov.conf` 模板，真实配置放在宿主 `~/.openviking/ov.conf` |
| `docker/openviking-rerank-adapter.py` | OpenAI 兼容 rerank 适配器：把 OpenAI rerank 协议转成火山知识库 `/api/knowledge/service/rerank` |

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

## 密钥存储

模板里开了 `encryption.api_key_hashing.enabled`：用户 key 以 Argon2id 哈希存在各账号的
`_system/users.json`，明文不可恢复。已有明文 key 会在服务启动时自动迁移（日志
`Migrated API key for user ...`），**客户端手里的 key 不需要更换**；但此后
`ov admin list-users --reveal-api-keys` 只能回 `key_prefix`，完整 key 只在创建用户或
重置时显示一次。改这一项前先做备份：迁移不可逆。

## 备份

- 脚本 `~/.openviking-backups/backup.sh`，由 launchd 每天 03:30 触发
  （`~/Library/LaunchAgents/com.shubinzhang.openviking-backup.plist`），保留最近 7 份，
  日志写在 `~/.openviking-backups/backup.log`。
- 热备份，服务不中断；排除 `data/bot/logs` 与 `studio-chrome-profile`，包含 `ov.conf`，
  产物权限 600。
- 冷备份（RocksDB/SQLite 要求一致时）：`COLD=1 ~/.openviking-backups/backup.sh`，
  会临时停容器、备份后拉起。
- 恢复：`docker stop openviking` → 把归档解包回 `~` → `docker compose -f docker-compose.orbstack.yml up -d`。

## 指标（Prometheus）

`server.observability.metrics.enabled=true` 后 `/metrics` 暴露 `openviking_*` 指标（HTTP 请求、
检索、队列积压、执行器、组件健康等）。该端点**不做鉴权**，发布到 `1933` 即内网可见，别把它
暴露到公网。抓取配置与看板用仓库 `examples/grafana/`（`prometheus.yml`、
`docker-compose.yml`、预置 dashboard）。

容器日志：compose 里两个服务都设了 `logging` 上限（`json-file`，`max-size 50m` ×
`max-file 3`）。`ov.conf` 的 `log.output=stdout`，此时 `log.rotation` 管不到 docker 的
json 日志，必须靠这个上限，否则容器日志会无限增长。

### 已生效的调优项

| 配置 | 值 | 原因 |
| --- | --- | --- |
| `retrieval.apply_rerank_to_context` | `true` | 让 `search(mode="context")` 也用 rerank，否则 context 模式仍按向量分排序（每请求多一次 rerank 调用） |
| `storage.agfs.pathlock.provider` | `sqlite` | 单机本地盘用 SQLite 路径锁，避免递归扫描业务目录；锁库在 `data/_system/pathlock/pathlock.db` |
| `query_planner` | 显式配置（同 `vlm`，`thinking=false`） | 不配会隐式回退 `vlm`，口径不清 |

## Rerank（知识库适配器）

火山已弃用向量库的 `POST /api/vikingdb/rerank`（任何调用都返回
`400 Rerank is temporarily unavailable in VikingDB. Please use Knowledge Base`），
而 OpenViking 的 `rerank.provider="vikingdb"` 仍打这个老端点，且
[issue #5060](https://github.com/volcengine/OpenViking/issues/5060) 被关闭时维护者明确
「后面规划先下掉」，所以不要再用 `provider: "vikingdb"`，也不要改源码去等上游。

现用方案：compose 里的 `openviking-rerank-adapter` 容器把 OpenAI rerank 协议翻成知识库
`POST https://api-knowledgebase.mlp.cn-beijing.volces.com/api/knowledge/service/rerank`
（AK/SK 签名 `service=air`、`region=cn-beijing`、字段 `datas/rerank_model`、响应
`data.scores`），OpenViking 侧用内置的 `provider: "openai"` 指过去。好处：不动源码，
升级 OpenViking 不受影响；适配器挂了只会让检索退回向量分数，不会报错。

```bash
# 凭据（600，不入库）：火山控制台右上角账号名 → 密钥管理 → 新建密钥
cat > ~/.openviking/rerank-kb.json <<'JSON'
{"ak": "<AK>", "sk": "<SK>"}
JSON
chmod 600 ~/.openviking/rerank-kb.json
```

`ov.conf` 对应段落（`api_base` 用 compose 里的服务名，只在内网可达，不发布端口）：

```json
"rerank": {
  "provider": "openai",
  "api_key": "openviking-local-adapter",
  "api_base": "http://openviking-rerank-adapter:8808/v1/reranks",
  "model": "doubao-seed-rerank",
  "threshold": 0.1,
  "max_input_tokens": 2048,
  "timeout": 30
}
```

计费与规格：重排按 token 计（`重排模型-知识库【通用】`，0.0005 元/千 token）；知识库接口
单次最多 200 条候选、单条 4000 token。实测 20 条候选约 2580 token、130–190 ms。
账户若返回 `1000001` / `300004`，说明该账号的重排权限或配额有问题，走工单开白。

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
