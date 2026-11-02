# AiQQ AI 额度使用排查与优化交接

## 1. 目的

本文用于把当前只读排查结果交接给后续开发会话。重点不是供应商金额，而是确认 AiQQ 对 AI 的调用方式是否存在不必要的请求、输入上下文和推理消耗，尤其是普通群聊对话链路。

截至本文完成时，没有修改代码、配置或服务，也没有为排查触发任何真实 AI 请求。

## 2. 排查范围

- 运行服务：`aiqq.service`
- 当前进程启动时间：`2026-09-20 17:11:40 CST`
- `NRestarts=0`
- 日志统计截止约：`2026-09-22 14:32 CST`
- 当前 AI 后端：Codex SDK
- 当前模型：`gpt-5.6-sol`
- 主推理强度：`high`
- utility 推理强度：`high`
- 上游地址：第三方 OpenAI 兼容代理 `api.airoo.cc`

当前运行进程早于 `NovelAI快速生图` 的最新提交，因此运行日志不包含该新指令；关于快速生图的结论来自当前源码调用链分析。

## 3. 核心结论

项目存在明显的 AI 额度浪费，主要不是重试或重复投递，而是以下架构问题：

1. 每次普通聊天都无条件发送最近 50 条完整数据库 wire record。
2. wire record 包含大量对回答没有直接价值、且相互重复的原始字段。
3. 历史读取工具会累计完整历史页，并在后续模型请求中重复注入。
4. 每次普通聊天固定先执行一次独立 AI 审核，再执行正式聊天。
5. 审核、规划和正式聊天共用同一个高推理模型，没有任务分层。
6. 每轮聊天都启用联网、聊天读取和 GPT 图片动作 skill，而不考虑当前请求是否需要。
7. Codex SDK 已返回 usage，但业务层没有记录或汇总，无法准确归因消耗。
8. 同一用户快速修正上一条消息时，旧请求不会取消，两个高成本请求可能并行完成。

未发现以下问题：

- 没有 `codex_sdk_failed` 重试风暴。
- 可见的 70 次 `/v1/responses` 全部为 HTTP 200。
- 数据库事件没有重复投递记录。
- QQ 网关定时重连没有触发 AI 调用。
- 服务启动和健康检查没有自动调用 Codex。

## 4. 已确认的调用规模

当前运行窗口内：

| 调用来源 | 数量 | 说明 |
| --- | ---: | --- |
| 普通聊天主 turn | 16 个 | 进入正式 ChatAgent 的请求 |
| 主 turn 内 `/responses` | 70 次 | 每个聊天 1 至 9 次，平均 4.375 次 |
| 普通聊天输入审核 | 17 次 | 16 次通过，1 次审核拒绝 |
| `NovelAI提示词` | 10 次命令 | 每次审核、标签规划、提示词编写，共 3 个模型 turn |
| `NovelAI生图` | 32 次命令 | 每次生成前执行 1 个图片审核 turn |

这里的 70 次是 ChatRuntime Python 代理可以直接观测到的主聊天请求。审核和 NovelAI 提示词任务由独立 Codex 子进程直连上游，不会出现在这 70 条 `httpx` 日志里。

普通聊天中超过基准一次请求的 54 次，主要来自历史读取、图片读取、图片查看、联网搜索以及 Codex agent 的后续推理轮次。它们不是 HTTP 失败重试，但当前实现放大了工具调用成本。

## 5. 最严重问题：默认上下文过量

### 5.1 当前行为

`ConversationWorkflow` 每次审核通过后，无条件读取最近 50 条消息：

- `src/aiqq/logic/conversation.py:112`
- `src/aiqq/bootstrap.py:129`

`ChatAgent` 把这些记录的完整 wire value 放入 `reference_material.group_messages`：

- `src/aiqq/services/agents/chat.py:83`

完整 wire record 由以下内容组成：

- 数据库 `group_messages` 整行
- 整行中的 `payload_json` 字符串
- 同一 payload 的解析对象
- 附件原始 JSON 和解析对象
- 最新网关事件的 `raw_text`
- 同一网关事件的解析 `envelope`
- QQ 消息投递尝试、请求参数和返回结果
- 派生字段

构造位置：

- `src/aiqq/database/group_messages.py:694`
- `src/aiqq/database/group_messages.py:715`

### 5.2 实测体积

对当前 16 个普通聊天的初始应用层 JSON 进行只读重建：

| 指标 | 完整 wire | 精简语义记录 |
| --- | ---: | ---: |
| 平均每轮 | 553,671 bytes | 12,843 bytes |
| 最小 | 394,102 bytes | 约 9 KB |
| 最大 | 825,892 bytes | 约 17 KB |
| 16 轮合计 | 约 8.9 MB | 约 0.21 MB |

精简版本只保留：

```text
record_id, role, sender_name, sent_at, content, message_type
```

即使补充 `reply_summary`、`has_image`、`recall_state`、`progress` 和附件索引，体积仍会远低于完整 wire。当前默认输入大约是精简语义输入的 43 倍，约 97.7% 的字节可以从普通回答路径移出。

日志中的 `source_char_count` 只统计正文字符，不能反映真实模型输入。例如日志显示正文只有 1,363 字符的一轮，实际初始 JSON 约为 394 KB。

### 5.3 当前消息也重复

`ChatAgent` 同时发送：

- `user_input`
- `current_message` 的完整 wire record

位置：`src/aiqq/services/agents/chat.py:102`。

当前消息平均完整 wire 约 15.5 KB。用户正文会同时出现在 `user_input`、数据库行、`payload_json`、解析 payload、原始网关事件和解析 envelope 中。

## 6. 历史工具的重复注入

### 6.1 当前行为

历史脚本每次读取一页后，ChatRuntime 会把完整结果追加到 `_history_context`：

- `src/aiqq/services/ai/chat_runtime.py:157`

后续每次 `/responses` 请求都会附加 `_history_context` 中的全部历史页，而不是只附加刚取得的新页面：

- `src/aiqq/services/ai/chat_runtime.py:248`

因此页面越多，后续请求重复携带的数据越多。如果 Codex 本身已经保留前一轮上下文，旧页面还会再次以新的 user message 注入。

### 6.2 实测历史页大小

两个请求要求遍历某群成员约 150 条发言。对应记录的完整 wire 与精简投影如下：

| 查询 | 完整 wire | 精简语义记录 |
| --- | ---: | ---: |
| 150 条记录 A | 2,578,100 bytes | 46,624 bytes |
| 150 条记录 B | 1,075,588 bytes | 39,871 bytes |

这还没有计算这些页面在多个模型轮次中的重复发送。

当前限制允许：

- 最多 3 页历史，每页 50 条
- 最多 8 个工具轮次
- 最多 24 个模型请求
- 最多 16 次搜索

相关常量位于 `src/aiqq/services/ai/chat_runtime.py:27` 和 `src/aiqq/services/chat_read.py:15`。

## 7. 普通聊天固定使用两阶段 AI

每个普通聊天首先调用 `PromptAuditAgent`：

- `src/aiqq/logic/conversation.py:95`
- `src/aiqq/services/agents/prompt_audit.py:54`

通过后才调用正式 `ChatAgent`：

- `src/aiqq/logic/conversation.py:161`

安全审核本身不是无意义功能，但当前实现存在资源分配问题：

- 与主聊天共用 `gpt-5.6-sol`
- 使用 `utility_reasoning_effort=high`
- 没有独立低成本模型
- 没有本地快速规则或分层审核
- 审核 usage 同样没有记录

不建议直接删除审核。更合理的方向是使用低推理、低成本的专用分类器，或者将安全分类与其他必需的轻量规划合并。

## 8. 所有对话都启用完整能力集

`ChatAgent` 每轮都传入：

- `enable_web_search=True`
- `enable_gpt_image_skill=True`
- `chat_read_session`

位置：

- `src/aiqq/bootstrap.py:120`
- `src/aiqq/services/agents/chat.py:119`

Codex 输入因此始终带有：

```text
$aiqq-chat-read
$aiqq-gpt-image
```

位置：`src/aiqq/services/ai/codex_sdk.py:273`。

其中 GPT 图片动作规则已经完整写在 `CHAT_INSTRUCTIONS` 中：

- `src/aiqq/services/agents/chat.py:47`

`aiqq-gpt-image/SKILL.md` 又描述了一遍相同协议。它不会直接生图，只负责填写 `image_action`，因此属于重复说明和不必要的默认 skill 激活。

启用 web search 或 GPT image skill 还会强制选择主 `reasoning_effort`：

- `src/aiqq/services/ai/codex_sdk.py:290`

当前主 effort 和 utility effort 都是 `high`，所以所有普通回答、审核、NovelAI 规划和提示词编写都在高推理档运行。

## 9. 图片问题的工具轮次偏多

Codex 图片读取通常需要经历：

1. 模型决定执行历史或图片脚本
2. command execution
3. image view
4. 模型继续生成答案

日志中多条图片相关问题产生 3 至 6 次 `/responses`。当前图片读取结果只在单个请求会话内缓存：

- `src/aiqq/services/chat_read.py:149`

明确附在当前消息上的图片，或明确引用的图片，可以由应用层在进入模型前解析并作为 `input_images` 提供，避免让模型先花轮次发现并读取它。

需要保留现有安全约束：模型只有实际收到并查看像素后才能描述图片，不能根据 URL、文件名或元数据猜测。

## 10. 新线程策略的取舍

当前每个模型 turn 都创建全新的临时 Codex thread：

- `src/aiqq/services/ai/codex_sdk.py:327`

优点：

- 不会跨群或跨用户泄漏状态
- 不会形成无限增长的隐式模型会话
- 容易取消和清理

缺点：

- 每次必须重新发送系统指令和群聊上下文
- 无法依赖服务端会话状态减少输入

不建议仅为节省额度直接改成长生命周期线程。优先保留无状态边界，同时压缩应用管理的上下文。

## 11. 输出和 usage 可观测性缺失

`OPENAI_MAX_OUTPUT_TOKENS` 存在于配置：

- `src/aiqq/config.py:163`

但 `_build_ai_backend()` 构造 Codex SDK 后端时没有传入该参数，Codex thread options 也没有输出 token 限制：

- `src/aiqq/bootstrap.py:275`
- `src/aiqq/services/ai/codex_sdk.py:301`

Codex SDK 的 `turn.completed` 已提供 usage：

- `src/aiqq/services/ai/codex_sdk.py:435`

最终也放进了 `AgentTurnResult.usage`，但所有 agent 都只读取 `result.text`，没有保存 usage。当前无法回答：

- 每种业务用途实际用了多少 input/output/cached tokens
- 一个工具型 turn 的 usage 是否为所有 `/responses` 的聚合
- 完整 wire 中有多少输入获得缓存
- 哪个用户工作流是主要消耗来源

## 12. 并发修正造成重复工作

`GroupMessageHandler` 只按 `(group_id, message_id)` 去重：

- `src/aiqq/interfaces/qq/handlers.py:330`

它没有维护 `(group_id, member_openid)` 维度的活动请求。实测中，同一用户在约 22 秒内发送了一条含错字的图片问题和修正版，两个请求都完成了完整图片分析并分别回复。

不能简单地永久采用“新消息取消旧消息”，因为用户可能连续提出独立问题。可考虑：

- 同用户同群只允许一个活动请求，并为新请求提供显式覆盖语义
- 增加短时间撤销/替换窗口
- 提供 `/取消` 或面板取消按钮
- 至少记录同用户并发 turn，便于后续统计

## 13. NovelAI 工作流的次要问题

这不是普通对话的首要问题，但后续应一起处理。

`NovelAI提示词`当前调用链：

1. 图片输入审核
2. Danbooru 查询规划
3. Danbooru MCP 查询，不是 AI turn
4. 最终三套提示词编写

相关位置：

- `src/aiqq/logic/novelai.py:172`
- `src/aiqq/services/agents/novelai_prompt.py:113`

审核和查询规划可以考虑合并为同一个结构化 turn，输出安全结论及查询词。

`NovelAI快速生图`当前调用链至少包含：

1. 描述审核
2. 查询规划
3. 提示词编写
4. 应用用户画风后的最终提示词审核
5. NovelAI MCP 生图

相关位置：`src/aiqq/logic/novelai.py:282`。

最终审核有安全价值，但对于由受控 agent 生成、未经过用户修改的第一套提示词，可以研究使用本地静态校验或轻量审核，避免再次调用同一个高推理模型。

## 14. 推荐目标架构

### 14.1 对话输入合同

默认只发送精简记录：

```text
record_id
role
sender_name
sent_at
content
message_type
reply_summary
has_image
image_attachment_indexes
recall_state
progress
```

原始 payload、网关事件、版本、投递尝试和完整附件元数据改为按需工具读取。

建议默认最近记录数先从 50 调整为 12 至 20，并通过测试验证群聊上下文质量。不要直接删除历史能力。

### 14.2 工具结果合同

- 默认历史查询返回精简语义记录。
- 只有用户明确询问原始事件、版本或投递状态时才返回完整 wire。
- 每一页只注入模型一次。
- `_history_context` 不应在每轮重新附加全部旧页面。
- 对同一 query/cursor 做请求内去重。

### 14.3 后端分层

- 普通文本回答：直接 Responses 调用，精简上下文，严格输出上限。
- 安全审核：低推理专用模型或轻量分类器。
- 历史/图片工具请求：直接 Responses function calling 或受限 Codex runtime。
- 复杂联网请求：才启用 web search。
- 图片生成动作：按明确生成/编辑意图启用，不要每轮注入 skill。

仓库已经有 `ResponsesBackend`：

- `src/aiqq/services/ai/responses.py`

它支持 `max_output_tokens`、web search 和 read tools，可作为简单聊天或 utility turn 的候选后端。不能未经测试直接全局切换，因为当前 Codex runtime 还负责聊天读取隔离和图片查看语义。

### 14.4 可观测性

每个逻辑 AI turn 至少记录以下非敏感字段：

```text
request_id
purpose: audit | chat | novelai_plan | novelai_author | image_audit
model
reasoning_effort
application_input_bytes
provider_request_count
tool_round_count
history_page_count
image_read_count
input_tokens
cached_input_tokens
output_tokens
elapsed_ms
status
```

不得记录用户完整 prompt、签名 URL、图片二进制、API key 或原始 provider body。

## 15. 推荐实施顺序

### P0：补齐观测

1. 给 `AIBackend.run()` 增加 purpose/request context，或使用独立的安全 metrics callback。
2. 记录应用输入字节数、usage、工具轮次和 provider request 数。
3. 为普通聊天、审核和 NovelAI agent 分开统计。

### P1：压缩默认对话上下文

1. 新增稳定的 compact chat record DTO。
2. 普通聊天只使用 compact DTO。
3. 当前消息不再同时携带完整 wire 和重复 user input。
4. 完整 wire 保留给管理页面和按需 raw-history 工具。
5. 为普通问题、回复上下文、图片引用、撤回消息和进度消息补测试。

### P2：修复历史页重复注入

1. 只注入新读取的页面。
2. 历史工具默认返回 compact record。
3. 对 query/cursor/page 做请求内去重。
4. 收紧普通群聊的工具轮次和模型请求上限。

### P3：模型与能力路由

1. 审核和规划使用低推理 utility backend。
2. 简单聊天不启用 chat-read、web search 和 GPT image skill。
3. 明确附图由应用预加载。
4. 复杂历史、看图或联网请求才进入工具型 runtime。

### P4：并发控制

1. 记录同用户同群活动请求。
2. 设计取消、覆盖或排队语义。
3. 确保取消会终止 Codex 子进程和未完成的 HTTP 流。

## 16. 验收建议

优化完成后，至少验证：

- 自包含文本问题的初始应用输入显著小于当前值。
- 普通文本问题只产生一个正式回答模型请求，外加按设计保留的审核请求。
- 不需要历史或图片时，不创建 ChatRuntime 读取能力。
- 当前消息正文不会在 payload 中重复多份。
- 历史第二页不会导致第一页再次注入。
- 图片问题仍然只在实际取得像素后作答。
- 群、用户和请求之间没有状态泄漏。
- 撤回、进度消息、附件、回复摘要等必要语义仍然可用。
- 每个逻辑 turn 都能从日志或指标中看到 usage 和 provider request 数。
- 超时、取消和关闭仍然会终止子进程并释放并发槽。

## 17. 注意事项

- 不要把 70 个 `/responses` 全部简单定义为无效请求。工具调用本身可能必要，问题在于工具路径过重、页面重复注入和能力默认全开。
- 不要为了节省上下文而破坏图片像素验证、安全审核或群隔离。
- 不要直接复用跨用户、跨群的长生命周期模型线程。
- 不要在日志中加入 prompt、原始聊天记录或凭据来换取可观测性。
- 下一会话开始实施前，应按 Trellis 流程创建任务，并先读取 backend 规范与相关测试约定。

## 18. 当前工作区状态

排查开始和文档创建前，代码工作区是干净的，`main` 相对 `origin/main` ahead 10。本文是本次排查唯一新增文件。
