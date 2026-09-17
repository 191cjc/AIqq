> R11 implementation update (2026-09-16): user explicitly said “开始改动”. The v4 code is implemented, validated (264 tests plus live two-image vision), and deployed at16:09 CST; prior plan-only statements below describe earlier rounds. Current evidence: research/chat-vision-plan/execution.md and *-result files. No persistent original images.

# GPT 图片服务失败修复需求

## 目标

**本轮最新范围（2026-09-16，R10）：用户重申 msgid 过期后主动 @ 原用户交付原回复和图片的要求，补齐已确诊的另一种过期返回，沿用既有修复及重启授权。NovelAI 和原图错误文案已于 10:54 部署，本轮保留其结果。**

恢复普通群聊中的 GPT 文生图、引用图片编辑及 `/GPT生图`；失败时能定位到真实原因，避免用户反复尝试和重复生成费用。用户已在核对参数和代理根因后明确要求“请修复并重启服务”，授权实施及服务重启。

## 已确认事实

- 2026-09-14 16:37:58，`api.airoo.cc/v1/images/generations` 返回 404；16:56:35，`/v1/images/edits` 返回 404。两次请求均已进入 GPT 图片服务。证据：`/var/lib/aiqq/aiqq.log:530`、`:556`。
- 同一天 15:06:32，改图接口曾返回 200。不能断言代理从未支持图片能力，也不能仅凭 404 确定根因。
- 当前配置使用独立图片密钥，图片地址继承普通 AI 地址，模型默认 `gpt-image-2`。使用该密钥只读查询 `/models` 返回 200，12 个结果中没有图片模型；这只是排查线索。
- 新实现没有保留错误正文，只记录异常类型、状态码和请求 ID：`src/aiqq/services/images/gpt.py:103`。旧实现有脱敏错误提取及对应测试：`gpt_image_service.py:64`、`tests/test_gpt_image_service.py:161`。
- 图片 SDK 当前设置 `max_retries=2`：`src/aiqq/services/images/gpt.py:57`。日志显示过 502 后自动重发；最近的 404 没有自动重试记录。
- 当前 121 项新架构单元测试通过，但 GPT 图片适配器测试没有覆盖真实 SDK 的 HTTP 失败路径，不能证明线上代理可用。
- 后续路由诊断明确返回当前密钥分组没有支持 `gpt-image-2` 的配置账号；新发布官方 SDK 的参数定义同时确认 GPT 图片不支持当前多传的 `response_format`。证据与限制见 `research/gpt-image-contract.md`；当前尚无真实生成成功结果。

## 需求与验收

| ID | 需求 | 可观察的验收结果 |
| --- | --- | --- |
| R1 | 保留足够且安全的图片调用诊断信息 | 每次调用可区分 generate/edit，并关联本地尝试 ID、模型、目标路径、耗时、结果及可用的上游请求 ID；失败有分类及脱敏后的上游错误摘要。 |
| R2 | 正确区分图片服务失败 | 文生图命令和普通对话遵循同一错误分类；404 表示接口请求失败，未经进一步证据不宣称模型不存在；群内展示简短固定文案。 |
| R3 | 控制真实生成次数并恢复偶发 502 | 用户于 2026-09-15 明确要求恢复 502 自动重试：仅收到 HTTP 502 时最多重试两次，间隔 0.5 秒、1 秒，每次业务调用最多三次 POST；其他状态、超时和连接故障不重试。保留每次重试的安全诊断；失败和取消释放本地任务占用，成功仅增加一次本地额度。 |
| R4 | 根据实际证据恢复代理调用 | 用户于 2026-09-15 授权采用 gpt-6-astra + Responses image_generation 工具。用当前图片密钥与真实 Codex 客户端接入，生成与参考图编辑均产出可解码图片，沿用既有 QQ 交付路径；不降级到已失败的 Images API，也不只凭 HTTP 200 判定成功。 |
| R5 | 防止误判与回归 | `/models` 缺少某模型不阻止启动/生图；日志和群回复不输出凭据、提示词、群消息、图片内容或带鉴权信息的 URL。 |
| R6 | QQ 上传前自动检查并压缩大图 | 用户于 2026-09-15 要求所有超过 2 MB 的图片在上传 QQ 前压缩。网页找图、GPT 和 NovelAI 共用此检查；不超过 2 MB 的图片保持原字节，超过的图片压缩后必须不超过上限，保持宽高比，输出 MIME/后缀一致；失败不能继续上传过大的原图。 |

## 范围

2026-09-16 追加 R9：恢复 NovelAI MCP 显式配置，能够实际生成有效图片；缺少配置和连接未就绪时给出明确文案，健康接口可观测其配置/就绪状态，配置接线纳入回归测试。具体执行与边界见 `research/novelai-restoration.md`。

2026-09-15 追加 R8：原图过期/失效、缺失、超时、访问受限、无效或过大等可识别原因应显示对应的简短原因和下一步建议，群内摘要与完整文案一致；未知或混合失败不猜测为过期。保留生成接口错误的原有分类，原图加载失败不得触发生图或占用额度。本轮仅完成方案、代码与离线验证，不部署。

2026-09-15 追加 R7：QQ 明确拒绝过期 msg_id 时，最终结果改用主动消息 @ 原用户回复，图片一起交付；超过 600 秒转入后台的任务完成后同样回复。每条被拒绝的消息最多主动补发一次，其他错误不新增重试。图片在尝试 QQ 发送前保存，主动发送受 QQ 权限与额度约束。沿用用户已授权的修复和重启范围。

- GPT 图片适配器、跨层错误契约、两个 GPT 调用入口、必要的回归测试和运维说明。
- 用 mock HTTP 传输验证真实 SDK 的请求和错误路径，再进行有次数限制的线上验收。
- 本任务不修改 NovelAI 的业务行为、AI 审核规则、群历史策略、菜单、数据库结构；不清理其他重构遗留文件。
- 代理或密钥的配置调整以实际诊断结果为依据。备用服务接入、自动切换模型、自动重放失败群消息不在本方案内。

## 完成标准

R1–R8 全部满足才可报告服务已修复。只有诊断代码和离线测试通过时，应报告“诊断能力已补齐”；代理仍报错或真实图片未成功交付时，任务仍未完成。上游账单结果与本地成功额度分别判断，不能承诺超时请求一定不计费。

## R10: Complete expired-message direct reply delivery

The user reiterates R7: both observed QQ expiry rejections must deliver the original answer and image with an original-member mention and without expired reply context. Terminal foreground delivery failure must not escape into gateway handling; no repeated generation/upload or ambiguous-send retries. Existing repair/restart authorization continues. See `research/qq-expiry-variants.md`.

## R11: Ordinary chat image understanding — proposal only

2026-09-16 latest v4: user confirms on-demand vision, skill/read scripts and scoped Codex tools; complete latest50 messages without10000-character trimming, older history on demand. Store complete group gateway events, message versions, observed recall/change lifecycle and attachment URLs/metadata. User explicitly rejects persistent original-image storage: no image cache or receipt-time prefetch; download only for the active read, clean temporary files afterwards, and ask the user to resend on confirmed expiry/unavailability. Keep existing QQ/media delivery behavior. Current proposal: research/chat-vision-plan/{prd,design,implement,storage-design}. User maintains plan-only; no application/config/skill changes, migration, model calls or restart are authorized by this planning request. Prior repair/restart approvals do not authorize R11 implementation.

## R12: Pure-image delivery — current authorized change

The user requests image messages contain only the image, including expired-msgid proactive fallback. Text replies retain @. R11 chat vision remains plan-only. See `research/qq-image-only-delivery.md`.
