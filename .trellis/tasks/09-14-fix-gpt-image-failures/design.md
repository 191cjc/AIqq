> R11 implementation update (2026-09-16): user explicitly said “开始改动”. The v4 code is implemented, validated (264 tests plus live two-image vision), and deployed at16:09 CST; prior plan-only statements below describe earlier rounds. Current evidence: research/chat-vision-plan/execution.md and *-result files. No persistent original images.

# GPT 图片失败修复设计

> 2026-09-15 追加用户需求：QQ 上传前对所有超过 2 MB 的图片进行自动压缩，当前交付层设计见 `research/qq-upload-compression.md`。图片生成通道仍采用下述 Responses 迁移。

> 2026-09-15 用户授权的后续迁移：图片调用改为 gpt-6-astra + Responses image_generation，当前生效设计见 `research/responses-migration.md`。下文 Images API 内容保留为先前修复记录；错误分类、额度、脱敏和仅 502 重试约束继续适用。

## 修复路径

先让单次调用留下可诊断证据，再按证据恢复代理或兼容参数，最后用真实图片验收。日志改进本身不等于修复 404。

```text
群聊生成 / 引用改图 / GPT 生图命令
  → 原有审核与额度流程
  → GPTImageService：仅 502 最多重试两次 + 尝试 ID + 脱敏诊断
  → 成功：原有 JPEG 规范化、计数及 QQ 发送
  → 失败：公共类型化异常 → 统一短文案；释放占用
```

## 1. 图片适配器拥有请求诊断

主要修改 `src/aiqq/services/images/gpt.py`。每个服务调用生成独立 `attempt_id`，使用 monotonic clock 记录耗时；记录开始及结束事件，覆盖 generate 和 edit。日志目标为真实使用的图片模型和 endpoint，不把整个 AppConfig、SDK 客户端或异常对象序列化。

建议稳定字段：`event`、`attempt_id`、`operation`、`model`、`endpoint`（仅 scheme/host/path）、`elapsed_ms`、`outcome`、`status`、`error_kind`、`provider_request_id`。失败额外附加提取并脱敏的 `provider_code`、`provider_type`、`provider_param`、`provider_message`。

错误处理规则：

- 优先解析 JSON 的 `error` 对象，也兼容顶层错误对象；仅提取字符串白名单字段。
- 摘要最多 1000 字符，压平控制字符；任意字段，包括上游 request ID，都要限长并清理换行。
- 屏蔽实际图片 API 密钥、Bearer token、常见 key/token 形式、带查询凭据的 URL；提示词及图片数据的回显应省略。只有无法还原用户输入的安全摘要才能进入日志，无法确定时标记 `detail_omitted`。
- HTML、非 JSON 或超大正文仅记录内容类型、长度及固定分类，不输出原始 HTML 或任意响应正文。
- 本地 attempt ID 和上游 request ID 分开命名，避免与现有 conversation request ID 混淆。
- 业务层可通过异常获得 attempt ID，用于关联该群聊回合的现有日志；QQ 中不展示代理错误正文。

旧模块的脱敏实现及测试作为参考，代码落在新包中，保持新入口的独立性。

## 2. 服务错误契约与用户提示

在 `src/aiqq/exceptions.py` 定义框架无关的 `ImageGenerationUnavailable`，字段为 `kind`、`attempt_id`、可选 `status_code` 和 `provider_request_id`。原 `GPTImageError` 作为其子类保留；OpenAI/httpx 类型和正文解析只存在于服务层。

| 分类 | 判断依据 | 群内提示方向 |
| --- | --- | --- |
| `authentication` | 401/403 | 图片服务暂不可用，需要管理员检查配置。 |
| `not_found` | 404；不进一步猜测模型/路由 | 图片服务请求失败，暂时无法生成。 |
| `rate_limited` | 429 | 图片服务请求受限，请稍后再试。 |
| `invalid_request` | 400/422 | 图片服务未接受本次请求。 |
| `timeout` | SDK 请求超时 | 图片服务响应超时，本次未收到图片。 |
| `connection` | 连接故障 | 暂时无法连接图片服务。 |
| `upstream` | 5xx | 图片服务暂时异常，请稍后再试。 |
| `invalid_response` | 2xx 但缺少数据、Base64/图片解码失败 | 图片服务返回了无法使用的结果。 |
| `unknown` | 其他预期外故障 | 使用现有通用失败提示。 |

将公共错误到 `ConversationResult` 的转换收敛在 `logic/image_generation.py` 中，由显式命令和 `ConversationWorkflow` 复用。每条提示不超过 50 字，`error_code` 使用稳定分类。未知故障保留兜底，避免业务层直接暴露 `str(exc)`。

原有审核拒绝、参考图失效、额度占用、QQ 上传失败各走原有分支，不将它们误判为 Images API 404；无需修改菜单或 QQ 展示结构。

## 3. 有限 502 重试与额度（2026-09-15 用户要求更新）

- GPT 图片 SDK 客户端继续设为 `max_retries=0`，由图片适配器仅在收到 HTTP 502 时最多重试两次，分别异步等待 0.5 秒、1 秒。一次业务调用最多三次生成/编辑 POST；其他状态、超时、断连、图片解析失败均不重试。其他模型与 NovelAI 客户端不受影响。
- 各次请求复用同一提示词、模型和参考图。`gpt_image_retry` 记录同一 attempt ID、502 请求的安全诊断、上游 request ID、重试序号和等待时间；每个业务调用仍只有一个开始和结束事件。后续超时等异常不能误带上一次 502 的状态和 request ID。
- 异步等待和重试请求均可取消；取消后不会继续发请求。维持原有请求超时设置。
- 不自动重放之前失败的群请求，不在同一业务尝试中更换模型/代理再次生成。
- 保留当前成功返回且规范化图片后才增加本地成功额度的顺序；异常和取消使用既有 finally 释放占用。
- 超时、取消、异常响应只说明本地未收到可用图片，不能据此判断上游任务未执行或账单未扣费。
- `asyncio.CancelledError` 记录终止状态后继续向上传播，不转换成业务成功或一般服务异常。

这项调整恢复历史 502 后成功的能力，不解决当前模型分组 404，也不改变超时不自动重试的约束。

## 4. 代理根因确认和恢复

后续核对已取得两项证据（详见 `research/gpt-image-contract.md`）：当前代理返回密钥分组没有支持 gpt-image-2 的配置账号；OpenAI 发布 SDK 3.13.0 的参数定义确认 GPT 图片不支持 response_format。实施应移除两个调用中的该字段，同时优先修复现有代理分组的图片账号/模型配置。省略该字段的路由探测仍返回相同 404，因此参数清理不能单独解决通道故障。

通过同一 GPTImageService 做受控验证：固定安全提示词文生图一次；成功后以该图编辑一次。真实调用另列出次数和结果，不作为自动单元测试或服务启动健康检查。可以合并到用户发起的群内验收，避免重复付费。

| 诊断结果 | 恢复措施 |
| --- | --- |
| 明确为模型映射/通道不存在 | 修复该密钥在现有代理的图片通道配置，或填写代理确认支持的 OPENAI_IMAGE_MODEL。 |
| 明确为权限/认证错误 | 核对图片密钥所属账户、可用通道和权限，并修改对应服务配置。 |
| 明确为请求参数不支持 | 按代理实际契约调整最小参数集合，增加契约回归测试。 |
| HTML 404、通用 not found、正文没有充分信息 | 携 operation、时间、endpoint、模型和 provider request ID 到代理管理端追查；不把它自动归为模型不存在。 |
| 代理确认 endpoint/base URL 不匹配 | 修正 OPENAI_IMAGE_BASE_URL 并在相同请求路径复验。 |
| 代理上游故障 | 等待/推动该通道恢复；仅有客户端日志修复时保留“上游待恢复”状态。 |

`/models` 只作辅助证据，其缺项不会成为启动失败、禁用图片能力或自动换模型的条件。若需要全新代理或密钥，作为实施过程中发现的外部依赖记录，不在方案阶段猜填。

## 5. 变更边界

| 文件 | 必要改动 |
| --- | --- |
| `src/aiqq/services/images/gpt.py` | 有限 502 重试、尝试生命周期、错误提取与分类、日志；必要时修改经确认的兼容参数。 |
| `src/aiqq/exceptions.py` | 可跨层消费的图片失败契约。 |
| `src/aiqq/logic/image_generation.py` | 共享错误结果转换及显式命令接入。 |
| `src/aiqq/logic/conversation.py` | 上下文生图消费同一错误类型并关联日志。 |
| `tests/unit/services/test_gpt_image.py` | 使用真实 AsyncOpenAI + httpx.MockTransport 验证 HTTP 契约、诊断、502 恢复/上限和其他错误不重试。 |
| `tests/unit/logic/test_image_generation.py`、`test_conversation.py` | 两入口的错误文案、错误码、额度和取消释放。 |
| `README.md` | 图片诊断步骤、错误日志字段和生图重试语义。 |

运行配置只有在诊断证据支持时才修改。无数据库迁移、无新自动探测服务；健康接口不会增加付费生图探针。

## 2026-09-15：过期 QQ 回复交付

本轮最新交付设计见 `research/qq-expired-reply.md`：发送适配器严格识别 SDK 的过期错误并按显式开关补发主动 @ 消息；渲染层先保存图片，再发送文字和图片；后台完成使用相同交付路径。保留 600 秒前台阈值、压缩规则和原请求的本地关联，不改变管理员主动/指定回复行为。

## 6. 验证与上线

2026-09-16 最新恢复设计见 `research/novelai-restoration.md`。本轮恢复 NovelAI 已获实施与上线授权，覆盖前一轮暂不部署的阶段限制。

2026-09-15 最新追加设计：原图错误分层传递与固定文案见 `research/reference-image-errors.md`。此次仅修改代码和离线测试，用户明确要求不动服务；下方上线流程不适用于本轮。

离线使用 mock HTTP 覆盖 401/403、404 JSON/HTML、429、5xx、超时、断连、无上游 request ID、异常 JSON、错误回显密钥、错误图片数据及成功 JPEG 规范化。计数实际请求，验证 generate/edit 的 502→200、502→502→200、连续 502 最多三次 POST、502 后非重试错误立即停止，以及等待重试时取消不会再 POST。

工作流验证两个入口的错误分类与额度不变性，取消后可接受下一个任务。随后运行 `tests/unit` 全部测试。完整仓库旧测试与本次故障分开记录，不借本任务清理旧重构代码。

发布前记录本次精确补丁、运行版本和原配置的私有备份；因工作区有其他未提交改动，回滚限定在本次改动，不能直接 reset 整个工作区。应用完成当前图片任务后安排重启，验证进程、QQ 网关、普通问答以及两种图片的真实交付。

真实调用的建议初始批次为文生图 1 次 + 成功后改图 1 次；失败立即保留证据并停止该批次。修正后确需复测时明确新的调用次数，避免无限试错。获得真实成功结果前不宣称恢复。

## 2026-09-16: Complete QQ expiry variants

Latest design: `research/qq-expiry-variants.md`. Recognize the two observed exact SDK expiry messages at the sender boundary and contain final foreground delivery errors. Preserve previous restoration behavior/configuration; scoped restart after verification.

## 2026-09-16: On-demand chat vision proposal (R11)

Latest v4 is in `research/chat-vision-plan/design.md` and `storage-design.md`: skill/read scripts, complete recent50 messages and older history lookup, raw events/message versions/observed lifecycle/attachment metadata. User removed persistent image bytes/cache/prefetch; images are downloaded temporarily on demand and confirmed expired/unavailable links prompt re-upload. Existing image-edit flow is documented in `image-edit-reference-flow.md`. This is plan-only; no source/config/skill changes, model calls or deployment.

## R12: Pure-image expired replies

Latest scoped correction: `research/qq-image-only-delivery.md`. Exclude media messages from mention-content injection; preserve text mentions and existing bounded retry. No R11 vision implementation.
