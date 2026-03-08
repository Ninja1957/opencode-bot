# opencode-bot

一个面向团队协作的 **Feishu ↔ OpenCode** 会话控制中枢。  
**核心亮点：手机端（飞书移动端）即可直接控制多个 OpenCode 会话。**
它解决的核心痛点是：

- OpenCode 会话分散在多终端，状态不可见
- 人在飞书里沟通，却要切回终端绑定/切换 session
- 云端部署后，密钥管理和多会话路由容易混乱

换句话说：你不用守在电脑终端，也不需要 SSH 回去操作，
在移动端就能完成会话查看、绑定切换、持续对话和更新跟踪。

`opencode-bot` 把这些问题统一收敛为一套可运维能力：

- 在飞书内查看并选择在线 OpenCode 会话
- 绑定后持续对话，支持随时切换/解绑
- 自动监听本机所有在线 session 更新并推送给用户
- 对未绑定用户推送“是否绑定该 session”按钮卡片
- 支持云端部署的外置配置和密钥隔离
- 把“终端能力”转为“移动端可控能力”，真正做到随时随地操作

---

## 1. 项目价值（为什么做这个）

如果你在做多任务并行开发，往往会同时开多个 OpenCode session。传统方式下：

- 会话在哪个终端、当前在跑什么，很难快速感知
- 业务沟通在飞书，控制操作在终端，来回切换成本高
- 运维层面容易出现“进程重复拉起、配置散落、密钥误提交”

本项目的目标是把 **“会话可见 + 会话可控 + 会话可运营”** 做成默认能力，支撑“云端统一控制多个 OpenCode”场景。

尤其在移动办公场景下，这个项目把手机端变成了 OpenCode 的轻量控制台：

- 在地铁/会议中也能通过飞书快速处理会话
- 不用登录服务器终端就能完成绑定、切换、追踪
- 运维与研发都能在统一入口完成协作

---

## 2. 功能总览

### 2.1 飞书侧能力

- 支持 HTTP 回调模式与长连接模式（`FEISHU_EVENT_MODE=http|long_conn`）
- `/session_list` (`/sl`) 查看在线会话并下发按钮卡片
- `/bind <session_id>` 绑定会话
- `/session_unbind` (`/su`) 解绑当前会话
- `/send <session_id> <内容>` 单次定向
- `@<session_id> <内容>` 单次定向快捷方式
- `/current` (`/c`) 查看当前绑定
- `/help` 查看帮助

### 2.1.1 移动端优势（重点）

- 飞书手机端可直接完成会话管理，不依赖本地终端
- 出差/会议中可即时处理 session 更新，决策链路更短
- 云端统一部署后，手机端就是“随身 OpenCode 控制面板”

### 2.2 OpenCode 侧能力

- 自动发现本机当前用户在线 session（CLI 模式）
- 会话标题增强（通用标题自动回退为最近 user 内容摘要）
- 在线/离线状态缓存与查询

### 2.3 会话运营能力

- 绑定关系持久化（SQLite）
- 消息幂等去重（避免重复处理）
- 轮次日志记录（请求/响应追踪）
- 全会话更新广播（可按绑定状态推送文本或绑定引导卡片）

---

## 3. 架构概览

- `src/opencode_bot/main.py`：启动入口（HTTP/长连接模式分流）
- `src/opencode_bot/feishu_long_connection.py`：飞书长连接事件入口
- `src/opencode_bot/api.py`：HTTP 事件入口
- `src/opencode_bot/service.py`：命令解析、绑定逻辑、消息路由
- `src/opencode_bot/opencode_client.py`：OpenCode 会话发现与消息发送
- `src/opencode_bot/session_registry.py`：在线会话扫描与状态聚合
- `src/opencode_bot/session_monitor.py`：会话更新监听与广播推送
- `src/opencode_bot/feishu_client.py`：飞书消息/卡片发送
- `src/opencode_bot/storage.py`：SQLite 持久化（绑定、peer、去重、日志）

---

## 4. 配置管理（重点：云端可配置）

配置加载优先级（高→低）：

1. 系统环境变量
2. `BOT_CONFIG_PATH` 指向的配置文件
3. `config/bot.env`
4. `.env`

也就是说，你可以把 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 完全放在云平台环境变量或挂载文件里，不进仓库。

### 4.1 配置模板

- 本地模板：`.env.example`
- 云端推荐模板：`config/bot.env.example`

### 4.2 关键配置项

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_VERIFY_TOKEN`
- `FEISHU_EVENT_MODE`（`http` 或 `long_conn`）
- `OPENCODE_TRANSPORT`（默认 `cli`）
- `OPENCODE_DB_PATH`
- `OPENCODE_WATCH_ENABLED`

---

## 5. 快速启动

### 5.1 安装

```bash
pip install -e .
```

### 5.2 准备配置

本地开发：

```bash
cp .env.example .env
```

云端部署（推荐）：

```bash
mkdir -p config
cp config/bot.env.example config/bot.env
export BOT_CONFIG_PATH="/absolute/path/to/config/bot.env"
```

### 5.3 启动服务

前台运行：

```bash
python3 run.py
```

推荐后台管理：

```bash
scripts/opencode-botctl.sh start
scripts/opencode-botctl.sh status
```

可用命令：

- `scripts/opencode-botctl.sh start`
- `scripts/opencode-botctl.sh stop`
- `scripts/opencode-botctl.sh restart`
- `scripts/opencode-botctl.sh status`
- `scripts/opencode-botctl.sh log`

---

## 6. 飞书接入说明

### 6.1 长连接模式（推荐）

适用于不暴露公网回调 URL 的场景。

1. 飞书开放平台启用事件订阅并切到“长连接接收事件”
2. 应用开通机器人能力及消息权限
3. 配置：
   - `FEISHU_EVENT_MODE=long_conn`
   - `FEISHU_APP_ID`
   - `FEISHU_APP_SECRET`
   - `FEISHU_VERIFY_TOKEN`（建议与平台保持一致）

### 6.2 HTTP 回调模式

1. 事件订阅 URL 指向：`http://<host>:8080/feishu/events`
2. 如本地调试需公网回调，可用隧道映射（如 ngrok）

---

## 7. 飞书使用流程（详细）

### 7.1 典型流程

1. 发送 `/session_list` (或 `/sl`)
2. 查看在线会话列表与按钮卡片
3. 点击按钮或 `/bind <session_id>` 完成绑定
4. 直接发普通文本，与当前绑定 session 持续对话
5. 需要切换时再 `/bind` 新 session
6. 结束时 `/session_unbind` (或 `/su`)

### 7.2 单次定向

- `/send <session_id> <内容>`
- `@<session_id> <内容>`

注意：只有上述两种格式会触发单次定向；普通两词消息不会再被误判成定向命令。

---

## 8. 会话监听与主动触达

当 `OPENCODE_WATCH_ENABLED=1`：

- 系统会轮询在线 session 的新增文本
- 已绑定该 session 的用户：收到直接更新
- 未绑定该 session 的用户：收到“是否绑定”按钮卡片

这让“多 session 云端控制”从被动查询升级为主动通知。

---

## 9. 云端上传与安全基线

仓库已忽略敏感与运行时文件：

- `.env`
- `config/bot.env`
- `data/`
- `run/`
- `*.db`
- `*.pid`

请勿提交真实密钥。建议把密钥放在：

- 云平台环境变量（首选）
- 挂载配置文件（配合 `BOT_CONFIG_PATH`）

---

## 10. 常见问题排查

- `/session_list` 无响应：先看 `scripts/opencode-botctl.sh status` 和日志
- 能收不能发：检查 `FEISHU_APP_ID/SECRET` 与机器人发送权限
- 会话为空：确认当前用户下存在 `opencode -s ses_xxx` 在线进程
- 事件不稳定：确认 `FEISHU_VERIFY_TOKEN` 已配置并与平台一致
- 进程残留：统一使用 `opencode-botctl.sh`，不要混用手工 `nohup/pkill`

---

## 11. 参考项目

- `nanoClaw`: https://github.com/ysz/nanoClaw
- `openclaw`
