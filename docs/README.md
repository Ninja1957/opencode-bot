## 运行指南
1. 拉取代码
`git clone git@github.com:Ninja1957/opencode-bot.git`

2. 安装环境
`conda create -n opcode-bot-py310 python=3.10.20`
`pip install -r requirements.txt `

3. 开始配置
- 飞书里在应用里新建自己的机器人，然后配置相关权限
- 仓库内拷贝.env.example到.env，并配置必填项，主要包含飞书相关的key

4. 运行
- python run.py (⚠️⚠️⚠️ 强烈建议在linux机器上使用tmux进行启动服务，参考：python3 run.py > full.log 2>&1)
- 运行成功后如下图

<img src="./images/success_verify.png" alt="opencode-bot icon" width="540" />



















