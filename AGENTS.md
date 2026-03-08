# OpenCode Bot 部署与运维 SOP

> 本文档记录 opencode-bot 的完整部署流程、排障经验和密钥配置。
> 仅供内部使用，请勿提交到公开仓库。

---

## 1. 项目概述

- **项目**: opencode-bot (飞书 ↔ OpenCode 会话控制中枢)
- **技术栈**: Python 3.8+ / conda / tmux / 飞书长连接
- **依赖**: lark-oapi >= 1.4.0

---

## 2. 环境准备

### 2.1 Conda 环境

```bash
# 查看可用环境
conda env list

# 确认 y6_test 环境已安装依赖（lark-oapi）
conda run -n y6_test python -c "import lark_oapi; print('OK')"
```

### 2.2 项目安装

```bash
cd /data/fuzhenxin/BOT-FOR-OPENCODE/opencode-bot
pip install -e .
```

---

## 3. 密钥配置

### 3.1 四个必需密钥

| 密钥名 | 用途 | 获取位置 |
|--------|------|----------|
| FEISHU_APP_ID | 飞书应用标识 | 飞书开放平台 → 应用 → 凭证与基础信息 |
| FEISHU_APP_SECRET | 飞书应用密钥 | 飞书开放平台 → 应用 → 凭证与基础信息 |
| FEISHU_VERIFY_TOKEN | 事件验证Token | 飞书开放平台 → 事件订阅 |
| FEISHU_ENCRYPT_KEY | 加密密钥 | 飞书开放平台 → 事件订阅 |

### 3.2 当前配置值

```
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_VERIFY_TOKEN=
FEISHU_ENCRYPT_KEY=
FEISHU_EVENT_MODE=long_conn
```

### 3.3 配置文件位置

```bash
# 项目根目录 .env 文件
/data/fuzhenxin/BOT-FOR-OPENCODE/opencode-bot/.env
```

### 3.4 配置模板 (.env.example)

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_VERIFY_TOKEN=xxx
FEISHU_ENCRYPT_KEY=xxx
FEISHU_EVENT_MODE=long_conn

BOT_HOST=0.0.0.0
BOT_PORT=8080
BOT_STORAGE_PATH=./data/opencode_bot.db
```

---

## 4. 服务启动

### 4.1 启动命令

```bash
# 方式一：直接启动（前台）
cd /data/fuzhenxin/BOT-FOR-OPENCODE/opencode-bot
conda run -n y6_test python run.py

# 方式二：tmux 后台启动（推荐）
tmux new-session -d -s opencode-bot "conda run -n y6_test python /data/fuzhenxin/BOT-FOR-OPENCODE/opencode-bot/run.py"

# 方式三：使用项目脚本
scripts/opencode-botctl.sh start
```

### 4.2 服务管理

```bash
# 查看 tmux 会话
tmux ls

# 进入服务会话（查看日志）
tmux attach -t opencode-bot

# 停止服务
tmux kill-session -t opencode-bot

# 或使用脚本
scripts/opencode-botctl.sh stop
scripts/opencode-botctl.sh restart
scripts/opencode-botctl.sh status
scripts/opencode-botctl.sh log
```

### 4.3 验证服务状态

```bash
# 检查端口监听
ss -ltnp | grep ':8080'

# 测试健康端点
curl -s http://127.0.0.1:8080/healthz

# 验证 Feishu Token 获取
conda run -n y6_test python -c "
import asyncio
from src.opencode_bot.config import Settings
from src.opencode_bot.feishu_client import FeishuClient
s = Settings.load()
c = FeishuClient(s)
t = asyncio.run(c._tenant_token())
print('Token OK:', t[:20] + '...')
"
```

---

## 5. 排障经验

### 5.1 常见问题快速定位

| 症状 | 检查项 | 命令 |
|------|--------|------|
| 发送消息无回复 | 1. 服务是否运行 2. 密钥是否配置 3. Token 是否获取成功 | `ss -ltnp \| grep ':8080'` / `curl localhost:8080/healthz` |
| 长连接模式报错"目标回调服务未在线" | 检查 FEISHU_EVENT_MODE 是否为 long_conn | `grep FEISHU_EVENT_MODE .env` |
| Token 获取失败 | FEISHU_APP_ID / APP_SECRET 是否正确 | 见上方验证 Token 命令 |
| HTTP 模式无法接收消息 | 需要内网穿透（ngrok）或公网服务器 | 飞书回调 URL 配置 |

### 5.2 排查步骤

1. **检查服务是否运行**
   ```bash
   ss -ltnp | grep ':8080'
   tmux ls
   ```

2. **检查配置是否加载**
   ```bash
   conda run -n y6_test python -c "
   from src.opencode_bot.config import Settings
   s = Settings.load()
   print('mode=', s.feishu_event_mode)
   print('app_id_set=', bool(s.feishu_app_id))
   print('app_secret_set=', bool(s.feishu_app_secret))
   print('encrypt_key_set=', bool(s.feishu_encrypt_key))
   print('verify_token_set=', bool(s.feishu_verify_token))
   "
   ```

3. **检查 Token 获取**
   ```bash
   conda run -n y6_test python -c "
   import asyncio
   from src.opencode_bot.feishu_client import FeishuClient
   from src.opencode_bot.config import Settings
   s = Settings.load()
   c = FeishuClient(s)
   try:
       t = asyncio.run(c._tenant_token())
       print('Token OK')
   except Exception as e:
       print('Token Error:', e)
   "
   ```

4. **查看实时日志**
   ```bash
   tmux attach -t opencode-bot
   ```

5. **检查数据库状态**
   ```bash
   conda run -n y6_test python -c "
   import sqlite3, pathlib
   p = pathlib.Path('./data/opencode_bot.db')
   if p.exists():
       conn = sqlite3.connect(str(p))
       n = conn.execute('select count(*) from processed_messages').fetchone()[0]
       print('processed_messages:', n)
       conn.close()
   else:
       print('db not found')
   "
   ```

### 5.3 配置问题对照表

| 配置状态 | 现象 | 解决方案 |
|----------|------|----------|
| APP_ID / APP_SECRET 为空 | Token 获取失败，消息无法回复 | 填写正确密钥 |
| EVENT_MODE=http | 长连接模式不生效 | 改为 long_conn |
| ENCRYPT_KEY 为空（长连接模式） | 飞书事件解析失败 | 填写飞书提供的加密密钥 |
| VERIFY_TOKEN 为空 | HTTP 模式验证跳过（可接受） | 可选填 |

---

## 6. 飞书平台配置

### 6.1 长连接模式配置步骤

1. 飞书开放平台 → 应用 → 事件订阅
2. 启用"长连接接收事件"方式
3. 配置以下权限：
   - 接收消息
   - 发送消息
   - 机器人能力
4. 获取并填写：
   - App ID → FEISHU_APP_ID
   - App Secret → FEISHU_APP_SECRET
   - 加密密钥 → FEISHU_ENCRYPT_KEY
   - 验证 Token → FEISHU_VERIFY_TOKEN

---

## 7. 快速恢复命令

```bash
# 一键重启服务
pkill -f "python.*run.py" 2>/dev/null
tmux kill-session -t opencode-bot 2>/dev/null
sleep 1
tmux new-session -d -s opencode-bot "conda run -n y6_test python /data/fuzhenxin/BOT-FOR-OPENCODE/opencode-bot/run.py"

# 验证
sleep 2 && ss -ltnp | grep ':8080'
```

---

> 更新时间: 2026-03-08
> 维护者: Sisyphus Agent
