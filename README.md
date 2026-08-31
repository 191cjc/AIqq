# AiQQ

一个使用腾讯 QQ 官方机器人 SDK 和 GPT Responses API 编写的群聊 AI 机器人。

## 1. 填写机器人凭据

复制环境变量模板：

```bash
cp .env.example .env
```

打开 `.env`，填写 QQ 开放平台提供的 `AppID` 和 `Secret`：

```dotenv
QQ_BOT_APPID=你的AppID
QQ_BOT_SECRET=你的Secret
AIQQ_QQ_HTTP_TIMEOUT_SECONDS=30
```

`AIQQ_QQ_HTTP_TIMEOUT_SECONDS` 控制 QQ 官方 SDK 的接口请求超时。图片文件较大时，
QQ 拉取临时图片可能超过 SDK 原先的 5 秒默认值，因此项目默认使用 30 秒；可配置范围为
5 到 120 秒。

继续在 `.env` 中填写 AI 接口配置：

```dotenv
OPENAI_API_KEY=你的代理服务SK
OPENAI_BASE_URL=https://api.airoo.cc/v1
OPENAI_MODEL=gpt-5.6-sol
OPENAI_TIMEOUT_SECONDS=180
AIQQ_AI_TOTAL_TIMEOUT_SECONDS=270
```

`OPENAI_API_KEY` 必须由你使用的代理服务提供。项目不会读取或复用 Codex 的认证信息。
普通 AI 对话使用 Responses API 流式接收，联网搜索和生成过程中的持续事件会刷新 HTTP
读取等待。模型在搜索形成大致方向或确认某个方向失败时，会先发送一句 60 字以内的
阶段说明；阶段说明使用普通 Markdown，不带按钮，最多发送 4 次。最终完整回答才附带
“清除记忆”和“NovelAI生图”按钮，并作为一轮写入对话记忆。

`OPENAI_TIMEOUT_SECONDS` 默认 180 秒，表示两次响应数据之间允许等待的最长时间，可配置
为 5 到 600 秒。QQ 被动回复有效期为 5 分钟，因此整个 AI 对话还受
`AIQQ_AI_TOTAL_TIMEOUT_SECONDS` 控制，默认 270 秒，可配置为 30 到 290 秒。

NovelAI 使用项目自带的 MCP 客户端，不依赖 Codex skill。在 `.env` 中填写：

```dotenv
NOVELAI_MCP_URL=https://你的MCP地址
NOVELAI_MCP_TOKEN=你的MCP访问令牌
AIQQ_PUBLIC_BASE_URL=https://你的域名/aiqq
```

也可以不直接填写 Token，改用 `NOVELAI_MCP_TOKEN_FILE` 指向服务器上的私密凭据文件。

不要把 `.env` 文件发送给其他人或提交到 Git。

## 2. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 3. 启动机器人

```bash
source .venv/bin/activate
python bot.py
```

出现 `AiQQ 已登录` 后，保持终端和程序运行。在 QQ 群中发送
`@机器人 你的问题`，问题会被转发给 GPT，模型文本随后回复到群里。

普通对话默认提供 Responses API 的项目级 `web_search` 工具。模型会在用户明确
要求联网、问题涉及最新信息或需要核实时自行搜索，并在回答中保留来源链接；
不需要最新资料的普通问题不会强制搜索。设置
`AIQQ_WEB_SEARCH_ENABLED=false` 可以关闭此能力。自动摘要和图片提示词审核不会
使用联网工具。

机器人会按“群 + 用户”隔离并持久保存上下文。每完成 10 轮对话，AiQQ
会调用模型生成累计摘要，并在摘要成功后删除这 10 轮原始消息。后续请求会携带
累计摘要和尚未归档的最近对话，服务重启后记忆仍然保留。

清除当前用户在当前群内的记忆：

```text
@机器人 清除记忆
```

发送 `@机器人 新对话` 具有相同效果。自动总结会额外产生一次模型调用。

机器人每次上线时会自动同步群聊指令面板。在 QQ 群中打开机器人的指令面板，
点击“清除记忆”并发送，也可以清除当前成员在当前群内的上下文。该指令对所有
群成员开放，但每个人只能清除自己的隔离记忆。

每条 AI 回复使用 QQ Markdown 展示，并在消息下方附带“清除记忆”按钮。点击
按钮会在当前输入框填入 `@机器人 清除记忆`，发送后即可清除当前会话的记忆。
其下方的“NovelAI生图”按钮会填入 `@机器人 NovelAI生图`，用户继续补充提示词
后发送即可开始文生图。

## NovelAI 文生图

指令面板中的“NovelAI生图”会把 `/NovelAI生图` 填入输入框，用户只需在后面
输入提示词。机器人同时兼容带斜线与不带斜线的命令：

```text
@机器人 /NovelAI生图 1girl, silver hair, red eyes, night city
```

该命令不会调用普通 GPT 对话，也不会读写对话记忆。GPT 会先独立审核成人内容，
通过后再检查提示词是否包含可生成的画面信息。包含中文字符时不会调用 NovelAI，
机器人会给出一条保持原意的英文提示词建议，并显示绿色标识的“使用建议”按钮。
腾讯 QQ 原生键盘只提供灰色与蓝色线框，因此该按钮使用绿色标识和灰色线框，与现有
蓝色按钮区分。按钮会把完整
生图命令与英文建议填入输入框，不会自动发送，用户可以检查或修改后重新提交。
有效性检查不通过或检查服务异常时也不会调用 NovelAI。用户输入整体只作为
`prompt`，项目不会解析模型、尺寸、Seed、负面提示词或其他生成参数。

当前生成配置固定为 `v4.5-full`、`1024x1024`、28 步、单张图片、质量与多样性
增强，并添加 `rating:general, safe, sfw`、通用高清与细节标签以及固定 SFW 负面
提示词。NovelAI 的质量开关还会添加官方质量标签。MCP 已公开 `steps` 参数，
项目会在每次生成时显式传入 28。

默认每个成员每天最多生成 100 张。机器人全局同一时间只处理一项生图任务；
上一项生成完成、失败或超时后，才会接受下一项。生成超时后不会自动重试，
以免产生重复费用。

部署到本机服务后，可以通过下面的地址检查进程是否已连接并启动：

```text
https://www.firesoul.cn/aiqq/health
```

## 常见问题

- 提示凭据错误：确认 `AppID` 与 `Secret` 属于同一个机器人，Secret 重置后要同步更新 `.env`。
- 收不到消息：确认机器人已加入群聊，并已开启群聊消息事件权限。
- 提示 AI 尚未配置：在 `.env` 中填写 `OPENAI_API_KEY` 后重启服务。
- AI 返回接口错误：确认密钥与 `OPENAI_BASE_URL` 属于同一个服务，并检查模型名称。
- 上下文没有延续：检查 `/var/lib/aiqq/memory.db` 是否可写，并查看服务日志。
- NovelAI 不可用：检查 MCP 地址、Token、外网连接和服务端 `generate_image` 工具。
- 图片无法显示：机器人会先让 QQ 从 `AIQQ_PUBLIC_BASE_URL` 拉取临时图片，再使用 QQ 富媒体消息发送；请确认该公网地址可访问，并将 `/aiqq/media/` 反向代理到本服务的 `/media/`。
- 关闭终端后离线：本机测试阶段这是正常现象；正式使用时需部署到持续运行的服务器。
