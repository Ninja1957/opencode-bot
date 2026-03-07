# opencode-bot

基于 `openclaw` 与 `nanoClaw` 思路，从 0 到 1 搭建一个可持续迭代的 opencode bot 项目。

## 参考项目

- `nanoClaw`: https://github.com/ysz/nanoClaw
- `openclaw`: 后续补充具体版本与关键实现点

## 项目目标（阶段 0）

- 搭建可协作的 Git 仓库与基础文档。
- 先明确 bot 的核心能力边界，再逐步补充功能设计与实现。
- 每次需求沟通后，持续同步更新本 README。

## 需求拆解框架（产品视角）

- 业务目标层：明确用户是谁、核心痛点是什么、成功指标是什么。
- 能力模块层：按模块拆解（模型管理、会话管理、命令执行、日志观测、权限与安全）。
- 用户故事层：按 `作为...我希望...从而...` 描述场景与价值。
- 交付层：按 `MVP / V1 / V2` 分层，并给出每项验收标准。

## 第一阶段（MVP）建议范围

- 可运行：能够本地启动并完成一次基础交互。
- 可配置：支持基础配置（至少模型与运行参数）。
- 可观测：记录关键日志，便于排错与回溯。
- 可扩展：目录结构支持后续新增命令与能力模块。

## 核心需求（当前确认）

以 `nanoClaw` / `nanobot` 思路为参考，构建一个“双向桥接”bot：

- 上层对接飞书：接收用户消息、展示可选会话、回传会话结果。
- 底层对接 opencode：感知当前所有在线 session，并将用户请求路由到指定 session。
- 对话转发机制：每轮对话完成后，将本仓库处理后的结果转发到飞书。
- 会话选择能力：用户可在飞书侧选择某个 session 并持续对话。

## 功能总览（当前版本）

- 飞书接入：支持 `url_verification`、消息事件、交互卡片回调。
- 在线会话发现：默认使用 `CLI` 模式，自动识别本机当前用户正在运行的 opencode 会话。
- 多会话主动管理：服务运行期间持续刷新在线状态，支持在线/离线缓存查询。
- 会话标题策略：优先使用 session 标题；若是通用标题，自动回退到最近 user 文本生成业务标题。
- 控制方式：支持持续绑定（`/bind`）与单次定向（`/send`、`@session_id`）。
- 按钮选会话：发送 `/sessions` 后自动下发按钮卡片，点击即绑定。
- 增量监听推送：可选开启会话增量监听，自动把新增文本推送到指定飞书目标。
- 追踪与幂等：SQLite 持久化绑定关系、消息去重、轮次日志，避免重复处理。

## 功能拆解（MVP）

- `Session Discovery`：拉取并维护在线 opencode session 列表（含 session 标识与状态）。
- `Session Binding`：将飞书会话与 opencode session 建立绑定关系（可切换、可查询当前绑定）。
- `Message Relay`：飞书消息 -> 本仓库 -> opencode；opencode结果 -> 本仓库 -> 飞书。
- `Round Tracking`：按轮次记录请求与响应，支持最小可追踪字段（时间、会话、请求摘要、响应摘要）。
- `Basic Reliability`：处理重复回调、防止重复转发、失败可重试并有日志。

## 关键用户流程

- 用户在飞书发起 bot 对话，请求查看可用 session。
- bot 返回在线 session 列表，用户选择目标 session。
- 用户继续发送消息，系统将消息路由到已绑定的 opencode session。
- opencode 返回当轮结果后，系统将结构化结果回传飞书。
- 用户可随时切换 session，后续消息按新绑定关系转发。

## MVP 验收标准（第一版）

- 能在飞书侧查看在线 session 并成功选择一个 session。
- 选择后可完成至少 3 轮稳定对话转发（飞书 -> opencode -> 飞书）。
- 每轮请求与响应有可检索日志，能按 session 维度追踪。
- 对重复事件具备幂等处理，不出现同一响应重复发送。

## 可靠性与集成约束（补充）

- 会话键规范：区分飞书 `chat_id`（群）与 `open_id`（私聊），避免错误绑定。
- 幂等策略：以飞书 `message_id` 作为幂等键，至少支持内存 + 持久化双层去重。
- 去重时机：在策略校验通过后、转发到 opencode 前执行去重，避免误丢可重试消息。
- 追踪字段：日志必须包含 `message_id`、飞书会话键、opencode `session_id`、轮次编号。
- 重试边界：转发失败允许重试，但同一 `message_id` 不能触发重复回复落飞书。
- 策略控制：预留 DM/群聊访问策略配置（例如 allowlist/open/disabled）作为 V1 兼容项。

## 当前待确认问题（需求访谈）

- 目标用户优先级：个人开发者、团队协作、还是运维场景。
- 第一阶段核心场景：最先要打透的 1 个高频任务。
- 交互入口优先级：CLI、Webhook、聊天命令。
- 必做功能 Top 3：作为 MVP 的硬性范围。
- 明确不做清单：避免第一阶段范围蔓延。

## 下一步建议（可逐步细化）

- 将“待确认问题”整理为第一版 PRD（目标、范围、验收标准、里程碑）。
- 产出 MVP 任务列表（按优先级和依赖关系排序）。
- 每轮需求讨论后，持续更新本 README 作为单一事实来源。

## MVP 架构设计（实现版）

- `HTTP Gateway`：提供 `/feishu/events` 接口接收飞书事件，`/healthz` 健康检查。
- `Relay Service`：解析命令与文本，完成 session 绑定与转发逻辑。
- `OpenCode Client`：拉取在线 session 并转发消息到指定 session。
- `Feishu Client`：将结果发送回飞书（文本消息）。
- `Storage`：SQLite 存储绑定关系、幂等键、每轮记录。

## 命令协议（飞书侧）

- `/sessions` 查看在线 session 列表
- `/bind <session_id>` 绑定会话
- `/send <session_id> <内容>` 单次定向到指定 session
- `@<session_id> <内容>` 单次定向到指定 session
- `/current` 查看当前绑定
- `/help` 查看帮助

说明：发送 `/sessions` 后，机器人会额外发送“会话选择按钮卡片”，点击按钮即可完成绑定并回执。

## 飞书机器人绑定（详细步骤）

1. 在飞书开放平台创建自建应用，启用机器人能力。
2. 在“事件订阅”中开启订阅，并将请求地址配置为：`http://<可访问地址>:8080/feishu/events`。
3. 在飞书侧设置 `Verify Token`，并与本服务 `FEISHU_VERIFY_TOKEN` 保持一致。
4. 给应用开通消息接收/发送相关权限，并发布到可用范围。
5. 将机器人添加到目标群聊或允许私聊。
6. 在群里发送 `/sessions`，确认能收到在线会话列表和会话选择按钮卡片。

如果你本机只监听 `127.0.0.1`，需要使用隧道工具（如 `ngrok`）映射到公网地址供飞书回调。

## 环境变量

- `BOT_HOST` 监听地址，默认 `0.0.0.0`
- `BOT_PORT` 监听端口，默认 `8080`
- `BOT_STORAGE_PATH` SQLite 路径，默认 `./data/opencode_bot.db`
- `FEISHU_APP_ID` 飞书应用 App ID
- `FEISHU_APP_SECRET` 飞书应用 App Secret
- `FEISHU_VERIFY_TOKEN` 飞书事件校验 Token（可选）
- `OPENCODE_BASE_URL` OpenCode API 基础地址，默认 `http://127.0.0.1:4096`
- `OPENCODE_TRANSPORT` 传输方式：`cli` 或 `http`，默认 `cli`
- `OPENCODE_BIN` opencode 可执行文件路径，默认 `~/.opencode/bin/opencode`
- `OPENCODE_DB_PATH` opencode 本地数据库路径，默认 `~/.local/share/opencode/opencode.db`
- `OPENCODE_LIST_SESSIONS_PATH` 会话列表路径，默认 `/api/sessions/list`
- `OPENCODE_LIST_SESSIONS_PATH_ALT` 会话列表备用路径，默认 `/api/claw/sessions/list`
- `OPENCODE_SEND_MESSAGE_PATH` 会话发送路径，默认 `/api/sessions/send`
- `OPENCODE_SEND_MESSAGE_PATH_ALT` 会话发送备用路径，默认 `/api/claw/sessions/send`
- `OPENCODE_API_KEY` OpenCode API Key（可选）
- `OPENCODE_REQUEST_TIMEOUT_S` OpenCode 请求超时秒数，默认 `30`
- `OPENCODE_WATCH_ENABLED` 是否开启在线会话增量监听，`1` 开启，默认 `0`
- `OPENCODE_WATCH_INTERVAL_S` 监听轮询间隔秒，默认 `5`
- `OPENCODE_WATCH_INCLUDE_ASSISTANT` 是否推送 assistant 消息，默认 `1`
- `OPENCODE_WATCH_INCLUDE_USER` 是否推送 user 消息，默认 `1`
- `FEISHU_NOTIFY_RECEIVE_ID` 监听推送目标（chat_id 或 open_id）
- `FEISHU_NOTIFY_RECEIVE_ID_TYPE` 推送目标类型，默认 `chat_id`

## 运行方式（本地）

1. 安装依赖（仅标准库，无额外依赖）
2. 设置环境变量并启动：

   `python run.py`

推荐用法：

- 前台启动：`python3 run.py`
- 后台启动：`nohup python3 run.py > /tmp/opencode-bot.log 2>&1 &`
- 停止服务：`pkill -f "python3 run.py"`

## 当前实现（MVP 已落地）

- 飞书事件接入：`POST /feishu/events`，支持 `url_verification` 与消息事件接收。
- 在线会话发现（CLI 模式）：通过 `ps` 扫描当前用户 `opencode -s ses_xxx` 进程，结合 `opencode.db` 读取标题。
- 会话绑定：飞书侧支持 `/sessions`、`/bind <session_id>`、`/send <session_id> <内容>`、`@<session_id> <内容>`、`/current`、`/help`。
- 双向转发：普通消息会转发到当前绑定的 opencode session，并将结果回发飞书；`/sessions` 会附带会话按钮卡片。
- 幂等与追踪：本地 SQLite 记录 `message_id` 去重、绑定关系、每轮请求响应日志。
- 在线会话管理：按当前用户进程实时发现 `opencode -s ses_xxx`，维护在线/离线状态缓存。

## 在线 Session 管理

- 数据源：本机进程 `ps -eo pid,uid,tty,args`，仅筛选当前 UID 的 opencode 进程。
- 会话识别：从命令参数提取 `-s ses_xxx` 作为在线 session_id。
- 元数据补全：通过 `OPENCODE_DB_PATH` 读取 `session` 表补全 title；若是通用标题（如 `New session`），自动回退到最近 user 文本生成业务标题。
- 状态机：每次 refresh 更新在线会话；未再次观测到的会话标记为 offline。
- 主动管理：CLI 模式下服务启动即持续刷新多会话状态，不依赖手动 `/sessions`。
- 统一查询接口：`GET /opencode/sessions`（缓存），`GET /opencode/sessions?refresh=1`（立即刷新），`include_offline=1` 返回离线缓存。
- 增量监听推送：开启 `OPENCODE_WATCH_ENABLED=1` 后，服务会轮询在线 session 新增文本并推送到 `FEISHU_NOTIFY_RECEIVE_ID`。

## 目录结构

- `src/opencode_bot/api.py`：HTTP API 入口（飞书事件处理）。
- `src/opencode_bot/service.py`：命令解析与转发编排。
- `src/opencode_bot/opencode_client.py`：opencode 会话列表与消息发送适配器。
- `src/opencode_bot/session_registry.py`：本机在线会话发现与状态聚合。
- `src/opencode_bot/feishu_client.py`：飞书发送消息适配器。
- `src/opencode_bot/storage.py`：SQLite 持久化（绑定、幂等、轮次日志）。
- `tests/test_service.py`：MVP 关键流程测试。

## 快速启动

1. 安装依赖

```bash
pip install -e .
```

2. 复制环境变量模板

```bash
cp .env.example .env
```

3. 编辑 `.env`（至少填写飞书相关）

```bash
FEISHU_APP_ID="cli_xxx"
FEISHU_APP_SECRET="xxx"
FEISHU_VERIFY_TOKEN="xxx"
```

4. 启动服务

```bash
python -m opencode_bot.main
```

5. 测试

```bash
pytest
```

## 常见问题排查

- 飞书回调失败：先检查飞书事件订阅地址是否能公网访问，再核对 `FEISHU_VERIFY_TOKEN`。
- `/sessions` 没有结果：确认本机当前用户有运行中的 `opencode -s ses_xxx` 进程。
- 能收消息但无法发回：检查 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 是否正确，以及机器人发送权限是否开通。
- 定向命令无效：确认 `session_id` 来自当前在线列表（`/sessions` 输出）。
- 监听推送没触发：确认 `OPENCODE_WATCH_ENABLED=1` 且 `FEISHU_NOTIFY_RECEIVE_ID` 已配置。

## 飞书侧命令

- `/sessions`：查看在线 session
- `/bind <session_id>`：绑定目标 session
- `/send <session_id> <内容>`：单次定向发送到指定 session
- `@<session_id> <内容>`：单次定向发送到指定 session
- `/current`：查看当前绑定
- `/help`：查看帮助
- 其他文本：转发到当前绑定 session
