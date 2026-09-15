# AiQQ

一个使用腾讯 QQ 官方机器人 SDK 和 OpenAI Codex Python SDK 编写的群聊 AI 机器人。

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

项目支持 QQ 的群消息全量事件，并将收到的群消息保存到独立 SQLite 数据库：

```dotenv
AIQQ_GROUP_MESSAGE_DB=/var/lib/aiqq/group_messages.db
```

数据库保存消息 ID、群与成员 OpenID、昵称、正文、消息类型、时间以及完整事件体；完整事件体
包含 QQ 下发的附件、提及、卡片和引用消息元素。消息按 `message_id` 去重，开启 WAL 模式，
文件权限设为仅服务账号可读写。项目不会自动清理历史记录，部署者需要按自身隐私和保留策略
管理该文件。除网关下发的群消息外，程序还会在 QQ 确认发送成功并返回消息 ID 后，主动将
机器人回复写入同一数据库，标记为 `BOT_MESSAGE_CREATE` 和 `is_bot=1`，同时保存实际展示文本、
消息类型、来源用户消息 ID、序号、按钮以及可用的图片附件信息。写库失败只记录服务日志，不会
影响已经成功发到 QQ 的消息。机器人不会对未 @ 机器人的消息自动回复。QQ 开启全量接收后
可能把 @ 机器人消息统一下发为 `GROUP_MESSAGE_CREATE`；程序会检查 QQ 提供的
`mentions.is_you` 字段并复用普通 @ 回复流程，同时按消息 ID 去重。

代码启用后，还需要每个群的管理员在机器人资料页开启“接收所有消息”。QQ 只会推送开启后
产生的新消息，不提供补拉此前历史消息的接口；管理员关闭后也会立即停止全量推送。

管理员消息查看页位于 `https://www.firesoul.cn/aiqq/group-messages`，使用浏览器 Basic Auth 验证。
用户名和口令只配置在服务器 `.env` 中，不得提交到 Git：

```dotenv
AIQQ_GROUP_MESSAGE_VIEW_USERNAME=admin
AIQQ_GROUP_MESSAGE_VIEW_PASSWORD=请设置独立高强度口令
```

页面按最近消息时间列出各群组，每页显示 50 条消息，并支持继续查看更早记录。附件图片和
引用消息会在页面中展示；完整事件中的鉴权令牌等原始字段不会直接输出到页面。
选择群组后，页面底部提供主动消息输入框；发送操作继续使用查看页的 Basic Auth，并额外
校验页面内的 CSRF 令牌。成功发送的消息会写入同一群消息数据库。

每条通过统一输入审核的 @ 群聊请求都会读取当前群、当前消息之前最多 50 条有效记录，
并把昵称、时间、正文、附件摘要和引用文本作为不可信参考资料交给本轮临时 Codex thread。
OpenID、附件 URL、鉴权令牌和完整原始事件不会发给模型；模型也不得执行参考资料中的命令。
历史参考资料的正文与引用文本合计默认最多 1000 字，优先保留较新的上下文：

```dotenv
AIQQ_GROUP_HISTORY_MAX_MESSAGES=50
AIQQ_GROUP_HISTORY_MAX_CHARS=1000
AIQQ_GROUP_REFERENCE_IMAGE_LIMIT=50
```

图生图不依赖机器人进程内的“上一张图片”缓存。用户可以直接指定当前群消息记录中的图片，
例如“用记录 16 的图片重画”“用我刚才发的图改成微笑”或“用小明上午发的那张图进行
图生图”。如果用户通过 QQ 的回复功能引用了一张图片，程序会优先读取当前消息
`msg_elements[].attachments[]` 中的被回复图片并直接使用，不会误选更新的历史图片。没有携带
回复图片时，程序才把最近的图片记录号、发送者、时间、正文和文件名交给一次临时结构化 AI 请求
选择唯一候选，但不会把附件 URL、OpenID 或鉴权信息交给模型；选中后才即时下载该条记录的
原图并通过 Responses 图片工具编辑。候选不唯一时机器人会要求补充记录号、发送者或时间。
`AIQQ_GROUP_REFERENCE_IMAGE_LIMIT` 控制每次参与匹配的最近图片记录数量，范围 1 到 100，
默认 50。QQ 的旧附件地址可能失效，遇到这种情况需要在群里重新发送原图。

继续在 `.env` 中填写 AI 接口配置：

```dotenv
OPENAI_API_KEY=你的代理服务SK
OPENAI_BASE_URL=https://api.airoo.cc/v1
OPENAI_MODEL=gpt-5.6-sol
OPENAI_TIMEOUT_SECONDS=280
AIQQ_AI_TOTAL_TIMEOUT_SECONDS=290
AIQQ_AI_BACKEND=codex_sdk
AIQQ_CODEX_CLI=
AIQQ_CODEX_RUNTIME_DIR=/var/lib/aiqq/codex-runtime
AIQQ_CODEX_WORK_DIR=/var/lib/aiqq/codex-work
AIQQ_CODEX_REASONING_EFFORT=high
AIQQ_CODEX_UTILITY_REASONING_EFFORT=high
AIQQ_GPT_IMAGE_SKILL_ENABLED=true
```

GPT 文生图默认复用上述密钥和代理域名，也可以独立配置：

```dotenv
OPENAI_IMAGE_API_KEY=
OPENAI_IMAGE_BASE_URL=
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_DRIVER_MODEL=gpt-6-astra
OPENAI_IMAGE_TIMEOUT_SECONDS=240
OPENAI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS=60
AIQQ_IMAGE_USER_DAILY_LIMIT=100
```

`OPENAI_IMAGE_API_KEY` 和 `OPENAI_IMAGE_BASE_URL` 留空时分别复用
`OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。图片服务通过真实 Codex SDK/CLI 调用 Responses API，
由 `OPENAI_IMAGE_DRIVER_MODEL` 指定的模型执行 `image_generation` 工具，工具的图片模型由
`OPENAI_IMAGE_MODEL` 指定。当前已验证 `gpt-6-astra` 的生成和参考图编辑；当前代理上的
`gpt-5.6-sol` 走 Responses Lite，会拒绝该图片工具。图片调用也需要安装 Codex SDK/CLI。

`OPENAI_API_KEY` 必须由你使用的代理服务提供。普通对话、内容审核和 NovelAI 提示词生成默认
通过 `openai-codex-sdk` 调用。SDK 每轮启动一次 `codex exec --experimental-json`，项目为代理
注册独立的 Responses provider，固定关闭 WebSocket 并使用 HTTPS 流。它不会读取管理员
`~/.codex` 中的账号、配置或个人 skill。

普通对话、输入审核、图片提示词审核和 NovelAI 提示词生成的每次模型调用都创建新的临时
thread，并使用该回合独立的 `CODEX_HOME` 和工作目录；结束后连同 SDK session 一起删除，
不恢复或延续此前模型会话。所有线程均使用只读沙箱、禁止审批，并关闭 shell、文件修改、
MCP、插件、浏览器、图片查看、原生生图和多代理能力。子进程只接收明确允许的环境变量，
不会继承 QQ Secret、NovelAI Token 等机器人凭据。普通对话按配置开放 Codex 原生网页搜索；
审核和 NovelAI 提示词调用不能直接触发生图。
普通联网对话还可按需执行一次精准图片检索。Codex 必须根据用户要求、结果标题和图片说明，
在最终回答中明确选定一张直接图片 URL；程序不会再按搜索顺序盲选。后续对话要求“发这张”时，
模型只能根据本轮群历史参考资料识别此前选定的图片 URL。只有该 URL 通过公网地址、真实格式、文件大小
和像素校验时，才会放在最终文字之后发送；
Codex 没有选图或无法确认匹配时只发送文字。找图只是下载公开搜索结果，不调用生图服务、
不占用每日生图额度，也不受全局生图锁限制。可通过 `AIQQ_WEB_IMAGE_SEARCH_ENABLED=false`
关闭，整次候选下载总时限由 `AIQQ_WEB_IMAGE_TIMEOUT_SECONDS` 控制，默认 20 秒。
模型完成一轮搜索时，会先发送
一句 40 字以内的阶段说明；阶段说明使用普通 Markdown，不带按钮，最多发送 4 次。正常 AI
对话会在同一次模型回答中同时生成完整正文和一条 50 字以内的直接摘要，不再额外请求摘要。
回复缩写缺失或失败时，程序会在本地生成不超过 50 字的回退文本。所有回复均使用普通
Markdown，不再自动添加 `text` 代码块包装。

普通对话中的上下文生图默认由 `AIQQ_GPT_IMAGE_SKILL_ENABLED=true` 开启。SDK 会为普通聊天
显式加载项目内的 `$aiqq-gpt-image` skill，把“按刚才那张的风格”等上下文指代整理成严格的
`image_action`；skill 不持有密钥，也不调用原生生图工具。实际生成复用 `/GPT生图` 的项目级
Responses 图片客户端，因此使用 `OPENAI_IMAGE_API_KEY`、
`OPENAI_IMAGE_BASE_URL`、`OPENAI_IMAGE_DRIVER_MODEL` 和 `OPENAI_IMAGE_MODEL`。程序会将结果规范化为 JPEG、校验文件大小和
像素数量，最多发送一张，并在最终文字之后通过 QQ 富媒体接口发送。“图呢”“重做”或
“按刚才的改成……”等续接请求只能使用本轮提供的当前群历史参考资料，普通图片咨询不会
触发生图。
统一输入审核会在同一次请求中判断普通文生图与参考图生图；参考图生图只以用户指定的当前群
消息图片记录为准，通过 Responses 输入图片和 `image_generation` 工具执行，并使用高保真输入模式。没有唯一匹配
的图片记录、图片附件已经过期或图片下载校验失败时不会开始生图，也不会占用当日额度。

`OPENAI_TIMEOUT_SECONDS` 默认 280 秒。使用 Codex SDK 时它是慢回合与前台等待阈值，
不再是中止任务的硬超时；程序同时参考 `AIQQ_AI_TOTAL_TIMEOUT_SECONDS`，取两者较小值作为
群聊前台等待时间。达到阈值后机器人会发送“任务处理时间过长，后续进展请看这里”和随机链接，
Codex 任务继续在后台执行，检索进展与最终完整回答会更新到同一个临时文件。进展页面每 3 秒
自动刷新，完成后停止刷新。显式取消或服务关闭仍会终止相应 SDK 回合。
原文超过 50 字的文本回复会附带“查看完整输出”按钮，打开一个随机 HTTPS 临时页面；
在页面中点击“复制完整原文”即可写入系统剪贴板，剪贴板接口不可用时会自动选中全文。
页面会安全解析 Markdown，并为标题、列表、代码块、引用、表格和链接提供阅读样式；
复制操作仍保留未经渲染的 Markdown 原文。
完整原文默认保留 1 小时，链接过期后返回 404。普通对话、NovelAI 提示词方案、
修改结果、拦截原因和其他命令说明均采用相同的 50 字摘要规则。`AIQQ_MAX_REPLY_CHARS`
只保留给其他可能需要分段的内部流程，不控制群内文本摘要。

普通 AI 对话的最终群聊回复会在摘要正文前 @ 原用户，但不携带 `message_reference`，
以避免电脑 QQ 重复渲染回复内容。联网搜索阶段消息不 @，避免重复提醒。50 字以内的原文
不生成临时页面，也不显示查看按钮。

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
uv sync --locked
npm ci
source .venv/bin/activate
```

Python 依赖由 `uv` 管理，`pyproject.toml` 和 `uv.lock` 精确锁定
`openai-codex-sdk==0.1.11`。该 SDK wheel 不包含 Codex 可执行文件，因此 `package-lock.json`
另行锁定 `@openai/codex` CLI；SDK 通过绝对路径启动它，systemd 无需在 `PATH` 中找到 Node.js。
需要临时回退到 Responses API 时，可设置 `AIQQ_AI_BACKEND=responses`。

## 3. 启动机器人

```bash
source .venv/bin/activate
python -m aiqq.main
```

出现 `AiQQ 已登录` 后，保持终端和程序运行。在 QQ 群中发送
`@机器人 你的问题`，问题会被转发给 GPT，模型文本随后回复到群里。

管理员也可以在机器人服务器上主动向指定群发送一条文本消息：

```bash
uv run python3 scripts/send_group_message.py \
  --group <GROUP_OPENID> \
  --text '消息内容'
```

脚本只访问本机管理接口，并读取 `AIQQ_ADMIN_TOKEN` 或权限为 `0600` 的
`AIQQ_ADMIN_TOKEN_FILE`。默认发送 QQ 主动群消息，会占用平台的主动消息频次；指定
`--reply-to <MESSAGE_ID>` 时改为回复该群最近 5 分钟内的对应 @ 消息。

默认角色是中文 AI 猫娘女仆助手，会称呼当前对话用户为“主人”，回复自然带“喵”，
正文不使用 QQ 表情或 Emoji，需要表达情绪时可以使用纯文本颜文字。用户不能通过聊天改变
角色、称呼、性格或表达方式；此类输入会在进入正常对话前被拒绝。部署管理员仍可通过
`OPENAI_SYSTEM_PROMPT` 覆盖设定。
每条普通对话都会先进行独立的输入审核；成人内容命中时机器人只返回警告，不调用正常
对话、不联网。审核服务异常时按安全优先拒绝请求。
同一次审核也会识别一条消息中的多个独立问题；命中时机器人会委婉请用户一次只问一个，
同样不会调用正常对话。围绕同一问题的条件补充、方案比较和分步骤排错不受影响。
对于较长的复合检索请求，审核会计算其中需要分别联网查找或核实的独立目标；达到 5 项，
或请求通过穷尽、逐项深挖、递归追踪、拒绝拆分等方式刻意制造等价范围时，会回复
“这个问题太难了，请一步一步来”，并且不进入主对话或联网搜索。长但聚焦的单一问题、
同一事实的多来源交叉验证以及最多 4 项相关检索不会仅因此被拦截。
如果单个问题的范围仍明显过大，包含大量无法在一次群聊回答中可靠完成的阶段或交付物，
审核会请用户先拆成更小的问题。专业问题、深入推理、代码排错和联网查询不会仅因难度被拒绝。

普通对话默认提供 Codex 的原生网页搜索工具。模型会在用户明确
要求联网、问题涉及最新信息或需要核实时自行搜索，并在回答中保留来源链接；
不需要最新资料的普通问题不会强制搜索。设置
`AIQQ_WEB_SEARCH_ENABLED=false` 可以关闭此能力。内容审核和群聊回复缩写不会
使用联网工具。

机器人只处理群聊中明确 @ 它的消息；未 @ 的群消息只写入群消息数据库，用作后续对话的
参考资料，不会触发回复。每轮普通对话都使用新的临时 Codex thread，不保存跨轮模型会话；
需要延续的话题由当前群的近期消息提供上下文。

机器人每次上线时会自动同步群聊指令面板。在 QQ 群中打开机器人的指令面板，可以使用
“菜单”“NovelAI提示词”和“NovelAI生图”三个指令。

普通文本回复下方附带“功能菜单”按钮；原文超过 50 字时，再增加“查看完整输出”按钮。
点击“功能菜单”会自动执行 `/菜单`，随后显示“NovelAI提示词”和“NovelAI生图”按钮；
也可以直接发送 `@机器人 /菜单` 打开该列表。这两个具体功能按钮只填充输入框，不会自动发送。
NovelAI 提示词方案会保留原有的三个“使用方案”和
“修改提示词”按钮，原文超过 50 字时在最后一行增加“查看完整输出”。

## GPT 文生图

GPT 生图只保留直接文本命令入口，可发送带斜线或不带斜线的命令：

```text
@机器人 /GPT生图 雨夜霓虹街道上的白猫，电影感光影
```

GPT 生图支持中文自然语言。项目会通过一次独立的临时 Codex thread，同时审核成人内容和
画面有效性；审核服务异常时按安全优先拒绝，通过后再调用 Responses 图片工具。审核不通过时会显示
绿色标识的“使用建议”按钮，点击后把安全建议填入新的 GPT 生图命令。生成参数固定为
`gpt-image-2`、`1024x1024`、中等质量、单张 JPEG，并额外加入 SFW 安全约束。请求阶段
会指定 JPEG 压缩质量 85；若代理仍返回其他尺寸或格式，项目会在上传 QQ 前规范化为
`1024x1024 RGB JPEG`。管理员可通过
`OPENAI_IMAGE_MODEL` 更换代理实际支持的图片模型，用户只能输入画面描述，不能传入模型、
尺寸、质量或其他生成参数。

图片专用 Codex thread 通过临时本机传输桥访问配置的 `/responses`，保留真实 SDK 请求头，
仅开放 `image_generation` 工具并强制调用；桥使用独立图片密钥，SDK 自身重试关闭。
程序要求流以 `response.completed` 结束，且包含完成的 `image_generation_call.result`，
再解码其中的 Base64 图片并校验。HTTP 200 或工具开始事件本身不算成功。
仅 HTTP 502 最多重试两次，分别等待 0.5 秒、1 秒；同一次业务调用最多三次上游 POST。
其他状态、超时、断连、流中断、无图或无效图片都不重试，也不自动回退 Images API。
`OPENAI_IMAGE_TIMEOUT_SECONDS` 是图片调用的总时限。失败或取消会释放本地生图占用，
不增加本地成功额度；上游是否计费以代理账单为准。

排查 GPT 图片失败时，可在日志中搜索 `event=gpt_image`。同一次图片调用通过 `attempt_id`
关联开始和结束记录，日志包含生成或编辑类型、模型、接口地址、耗时、HTTP 状态以及可用的
上游请求 ID；错误正文只保留脱敏后的摘要。普通对话的 `conversation_image_action_failed`
记录也会带相同 `attempt_id`。群内仅展示固定的简短错误说明。
若出现 403，应核对图片密钥权限和代理对官方客户端的限制；若出现不支持 `image_generation`
的 400，应核对驱动模型是否走 Responses Lite。旧 Images API 的模型分组 404 不代表
Responses 图片工具也不可用。`/models` 列表未包含图片模型只能作为线索；只有真实
生成和编辑成功才能证明通道恢复。修改 `.env` 后需重启服务使配置生效。

GPT 与 NovelAI 共用每日成功生成额度和全局单任务锁；任一服务正在生图时，另一服务也会
等待当前任务完成、失败或超时后才接受新任务。默认每个成员每天最多成功生成 100 张。

OpenAI 图片工具参数说明见[官方 OpenAI 文档](https://platform.openai.com/docs/guides/image-generation)。

## NovelAI 文生图

需要先把中文或自然语言画面描述整理成 NovelAI 提示词时，可发送：

```text
@机器人 /NovelAI提示词 一位站在樱花树下的白发少女
```

AI 会根据当前画面描述，在新的临时 Codex thread 中返回三套各有侧重的英文提示词。
三个绿色标识的“使用方案”按钮各自独占一行，点击后会把完整的
`NovelAI生图 <提示词>` 填入输入框，但不会自动发送。点击“修改提示词”后，可以在
自动填入的命令后继续写要求，例如“方案2改成夜景，并使用半身构图”；每次修改会返回
新的三套方案。修改会话与当前群和用户绑定，保存在 SQLite 中，15 分钟后失效。

提示词生成和每次修改都会使用新的临时 Codex thread；修改时通过当前群成员专属、15 分钟
有效的 SQLite 会话取得原方案。整理和修改提示词只调用 AI，不调用 NovelAI，也不占用每日
图片额度；只有实际发送 `NovelAI生图` 命令并成功生成图片时才计入额度。

指令面板中的“NovelAI生图”会把 `/NovelAI生图` 填入输入框，用户只需在后面
输入提示词。机器人同时兼容带斜线与不带斜线的命令：

```text
@机器人 /NovelAI生图 1girl, silver hair, red eyes, night city
```

该命令不会调用普通 GPT 对话。GPT 会通过一次独立的临时 Codex thread，同时审核成人内容、
画面有效性及是否包含中文。包含中文字符时不会调用 NovelAI，
机器人会给出一条保持原意的英文提示词建议，并在回复中直接显示绿色标识的“使用建议”按钮。
腾讯 QQ 原生键盘只提供灰色与蓝色线框，因此该按钮使用绿色标识和灰色线框，与菜单中的
蓝色功能按钮区分。按钮会把 `NovelAI生图 <建议提示词>` 填入输入框，但不会自动发送。
统一审核不通过或审核服务异常时也不会调用 NovelAI。任何提示词前置检查发生拦截时，
机器人都会显示具体的拦截原因并给出一条安全英文建议；当前回复中的“使用建议”按钮会直接
填入该建议。用户输入整体只作为
`prompt`，项目不会解析模型、尺寸、Seed、负面提示词或其他生成参数。

生图审核允许非色情语境下的正常露肤、泳装、运动服、赤足以及手部、腿部和足部特写；
不会仅因 `barefoot`、`foot focus` 或 `foot close-up` 等单一标签判定为恋物内容。只有出现
明确裸体或私密部位暴露、色情行为、性暗示、恋物意图或未成年人性化内容时才会拦截。

当前生成配置固定为 `v4.5-full`、28 步和单张图片。审核会同时判断构图方向：默认使用
`1024x1024` 方图，明确的全身构图使用 `832x1216` 竖图，躺姿使用 `1216x832` 横图；
同时出现全身和躺姿意图时以横图优先。生成请求会添加
`rating:general, safe, sfw` 以及仅包含成人内容防护的固定负面提示词。项目不会自动
追加画质、构图、肢体或瑕疵规避标签，NovelAI 的官方质量标签开关也保持关闭；多样性
增强保持开启。MCP 已公开 `steps` 参数，项目会在每次生成时显式传入 28。
`CFG Rescale` 固定为 `0.5`。

普通对话生图、`/GPT生图` 和 NovelAI 生图共用每日额度，默认每个成员每天最多成功生成
100 张。机器人全局同一时间只处理一项生图任务；
上一项生成完成、失败或超时后，才会接受下一项。GPT 文生图和改图收到 HTTP 502 时，
分别等待 0.5 秒、1 秒，最多自动重试两次；同一次任务最多发出三次请求，成功仅计一次
本地额度。其他 HTTP 错误（包括 404、429）、超时、连接故障及无效图片响应不自动重试。
重试写入 `gpt_image_retry` 日志，可通过同一个 `attempt_id` 关联开始、重试和结束记录。
生成超时后不会自动重试，以免产生重复费用。

部署到本机服务后，可以通过下面的地址检查进程、QQ 网关和 AI 后端状态：

```text
https://www.firesoul.cn/aiqq/health
```

`qq_gateway.connected` 为 `false` 时接口返回 HTTP 503，并记录最近断线时间、关闭码及累计
重连次数。SDK 通常会在约 5 秒内自动恢复；连续断线超过
`AIQQ_GATEWAY_RESTART_TIMEOUT_SECONDS`（默认 90 秒）时，机器人进程会退出并由 systemd
自动重启。`ai_backend=codex_sdk` 表示模型回合由机器人进程通过 Python SDK 启动，与管理员
当前打开的 Codex 窗口相互独立。

## 常见问题

- 提示凭据错误：确认 `AppID` 与 `Secret` 属于同一个机器人，Secret 重置后要同步更新 `.env`。
- 收不到消息：确认机器人已加入群聊，并已开启群聊消息事件权限。
- 提示 AI 尚未配置：在 `.env` 中填写 `OPENAI_API_KEY` 后重启服务。
- AI 返回接口错误：确认密钥与 `OPENAI_BASE_URL` 属于同一个服务，执行 `uv sync --locked` 和 `npm ci`，
  并检查 `OPENAI_MODEL` 与 `AIQQ_CODEX_CLI`。
- 群历史没有进入回答：确认群管理员已开启“接收所有消息”，并检查群历史统计日志。
- NovelAI 不可用：检查 MCP 地址、Token、外网连接和服务端 `generate_image` 工具。
- 图片无法显示：机器人会先让 QQ 从 `AIQQ_PUBLIC_BASE_URL` 拉取临时图片，再使用 QQ 富媒体消息发送；请确认该公网地址可访问，并将 `/aiqq/media/` 反向代理到本服务的 `/media/`。
- 完整原文打不开：确认 `/aiqq/reply/` 已反向代理到本服务的 `/reply/`，并检查随机链接是否已超过 `AIQQ_REPLY_TTL_SECONDS`。
- 关闭终端后离线：本机测试阶段这是正常现象；正式使用时需部署到持续运行的服务器。
