> R11 implementation update (2026-09-16): user explicitly said “开始改动”. The v4 code is implemented, validated (264 tests plus live two-image vision), and deployed at16:09 CST; prior plan-only statements below describe earlier rounds. Current evidence: research/chat-vision-plan/execution.md and *-result files. No persistent original images.

# 执行计划

## 当前追加阶段：QQ 上传前压缩

用户已明确授权所有超过 2 MB 的图片在 QQ 上传前压缩。按 `research/qq-upload-compression.md` 实施统一发送入口检查，完成定向/完整测试、独立复核、失败样本及公网下载验证后重启。

- [x] 压缩函数及统一发送接入，输出不超过上限、MIME/文件内容一致。
- [x] 边界与交付测试、独立复核通过；174 项完整单元测试通过。
- [x] 真实失败图片从 4999518 字节压到 385072 字节，保留 1797×1773；临时媒体本地/公网 GET 完整性通过。
- [x] 2026-09-15 16:31:05 +08:00 重启，PID 918169，QQ 连接与本地/公网健康正常；结果见 `research/qq-upload-compression.md`。

## 当前追加阶段：Responses 生图迁移

用户已接受 gpt-6-astra。按 `research/responses-migration.md` 实施图片专用适配器、SDK 专用模式和配置装配，复用原有错误契约与额度。完成服务测试、独立复核、完整单元测试及真实生成/编辑验证后重启，检查健康接口；不发送 QQ 消息，不改变 10000 字符历史预算。

- [x] 图片专用适配器、SDK 模式、配置装配及 README/spec 同步。
- [x] 独立复核及 160 项完整测试通过；最终配置修正另有 5 项定向测试通过。
- [x] 正式适配器文生图、参考图编辑各一次成功，均为 1024×1024 JPEG，耗时约 29/31 秒。
- [x] 2026-09-15 15:25:22 +08:00 重启，PID 898856；本地/公网健康 HTTP 200，QQ 已连接。
- [ ] 用户主动发起 QQ 请求，验收群内可见交付。

当前结果见 `research/responses-deployment.md`。下方旧 Images API 账号修复步骤保留为历史，不再是当前恢复通道的前置条件。

当前状态：用户已确认修复并重启服务，进入实施；真实图片验证按受控批次执行。用户未授权代发群消息，QQ 交付通过其后用户主动请求验收。

## 1. 建立可复现的失败测试

- [x] 读取本任务 PRD、设计和证据，确认工作区的已有未提交改动；保存本次改动前的文件基线，防止覆盖他人工作。
- [x] 以 `httpx.MockTransport` 驱动真实 `AsyncOpenAI`，分别构造 generate/edit 的 404 JSON 和 HTML 响应，复现错误正文丢失。
- [x] 构造 429/500/超时的单次请求测试，验证实际 HTTP 请求次数而不只检查 mock 方法次数。
- [x] 对错误日志加入密钥、提示词和 URL 凭据回显用例，测试脱敏与长度控制。

## 2. 最小代码修复

- [x] 添加公共图片失败类型和服务层分类，保留 `GPTImageError` 使用位置兼容性。
- [x] 在 GPTImageService 中增加独立 attempt ID、开始/结束日志、耗时、脱敏错误摘要及 provider request ID。
- [x] 将 GPT 图片 SDK 自动重试设为 0；给取消、无效图片响应补齐终止状态日志。
- [x] 根据已核对的 GPT 图片契约移除 generate/edit 中的 response_format，继续从响应的 b64_json 解码图片，并保留有效的 JPEG、压缩、质量及 input_fidelity 参数。
- [x] 由两个 GPT 业务入口复用同一错误结果转换，使用固定的短文案及稳定 error_code。
- [x] 测试所有失败及取消路径的额度和全局占用释放，保持审核、参考图校验和成功计数顺序。

## 3. 离线验证

```bash
.venv/bin/python -m unittest discover -s tests/unit/services -p 'test_gpt_image.py' -v
.venv/bin/python -m unittest discover -s tests/unit/logic -p 'test_image_generation.py' -v
.venv/bin/python -m unittest discover -s tests/unit/logic -p 'test_conversation.py' -v
.venv/bin/python -m unittest discover -s tests/unit -q
git diff --check
```

- [x] 若根据代理证据修改参数集合，补上 JSON 生成请求及 multipart 编辑请求的实际参数断言。
- [x] 校验日志不会泄露凭据、提示词、参考图片内容；两入口不显示代理正文。
- [x] 确认正常图片仍被规范化为单张 1024×1024 JPEG。

## 4. 实际根因与服务恢复

- [x] 核对生效的图片 endpoint、model、密钥来源，保持密钥仅在服务配置中使用。
- [ ] 优先解决现已确认的代理分组错误：为当前图片密钥所在分组配置支持 gpt-image-2 的上游账号/模型映射，或取得同服务已开通图片能力的密钥。未经授权不自动换用聊天密钥。
- [x] 受控文生图单次调用，保存 operation、HTTP 状态、耗时、脱敏详情及两种 request ID。
- [ ] 依据实际错误核对模型通道、权限、路由、参数；模型列表缺项不作为独立结论。
- [ ] 只修改证据指向的代码或配置；必要时将上游待处理项连同 request ID 交给用户，不能声称已修复。
- [ ] 文生图成功后以成功图片执行一次编辑；每个批次遇到失败即停止，复测前说明调整和次数。

真实生成不在自动测试、应用启动或健康检查中执行。首次实施阶段的真实调用预算按设计建议列出，由当时已有授权确定；本轮设计请求不产生付费请求。

## 5. 部署与最终验收

- [x] 将准确的诊断命令、日志字段和单次请求语义写入 README。
- [x] 记录部署补丁及私有配置备份；限定回滚文件，不动业务数据库和无关改动。
- [x] 在获准发布的时点应用修复并重启 aiqq；检查健康、QQ 连接和普通问答。
- [ ] 结合用户发起的群请求验收文生图、参考图编辑的可见图片，以及 `/GPT生图` 的既有入口。
- [ ] 分别记录离线测试结果、实际代理结果和 QQ 交付结果。对应 PRD R1–R5 逐项核对后，才标记完整修复。

## 回滚

### 2026-09-16：恢复 NovelAI

- [x] 备份并恢复显式 NovelAI 地址和凭据文件配置。
- [x] 增加配置/就绪错误、状态观测和启动接线回归；独立审查及 22 项定向、212 项完整单元测试通过。
- [x] 2026-09-16 10:54:11 +08:00 重启，PID 1185136；QQ 已连接，本地/公网 HTTP 200，NovelAI configured/ready 均为 true。
- [x] 一次真实生图成功：1024×1024 PNG、344313 字节，解码/目视及 QQ 大小检查通过；部署与回滚记录见 `research/novelai-restoration.md`。未发送 QQ 测试消息。

### 2026-09-15：原图错误文案（当时不部署；2026-09-16 随 NovelAI 恢复重启生效）

- [x] 按 `research/reference-image-errors.md` 保留下载错误分类，传递原图错误并映射固定文案。
- [x] 验证 404/410、超时、权限、无效图片、多候选与取消；原图失败不开始生图或计费额度流程。
- [x] 独立审查、完整单元测试和 diff 检查，更新 README 与 spec。
- [x] 确认服务 PID 和启动时间未改变，仅交付方案与代码。

### 2026-09-15：过期回复补发

- [x] 按 `research/qq-expired-reply.md` 接通发送边界、协议、图片保存顺序及后台完成交付。
- [x] Mock 验证过期错误、单次主动补发、@ 原用户、图片不重复上传及非过期错误不重试。
- [x] 独立审查、完整单元测试及 diff 检查；同步 README 和 spec。
- [x] 确认无运行任务后重启，检查本地/公网健康与 QQ 连接，记录线上验证限制。

- 日志/异常改动引发运行回归：恢复本次精确文件补丁并重启，保留诊断记录。
- 模型或图片地址修改后行为变差：只恢复对应原配置项，不输出密钥。
- 上游仍不可用：保留有效诊断修复，将外部未解决条件与下一步写入任务；不反复生成、不扩大到其他 AI 服务。

## 实施结果

代码和服务重启已完成；133 项单元测试通过，健康接口与 QQ 连接正常。真实文生图仍被代理分组配置阻断，详情见 `research/implementation-results.md`。

## 2026-09-15：恢复偶发 502 自动重试

用户明确要求恢复 502 重试，沿用现有修复与重启授权。本轮行为边界位于图片客户端：仅 502 最多重试两次，间隔 0.5 秒、1 秒；不修改业务流程、图片参数、密钥、模型或其他服务的重试策略。

- [x] 修改 `src/aiqq/services/images/gpt.py` 并补齐重试诊断；保留取消传播和单次终止事件。
- [x] 在 `tests/unit/services/test_gpt_image.py` 验证真实 SDK 请求次数、生成及编辑重放内容、重试上限、后续非 502 错误和取消。
- [x] 同步 README 和图片服务 spec，运行完整单元测试并交叉检查。
- [x] 备份本轮文件后部署重启，验证健康接口和 QQ 连接；不以单元测试通过宣称模型分组 404 已恢复。

结果：18 项图片客户端定向测试、139 项完整新架构测试通过；独立审查无阻塞项。2026-09-15 11:03:45 +08:00 重启，PID 828706，active/running，NRestarts=0。本地和公网健康接口 HTTP 200，QQ connected=true，数据库打开。未新增真实生图请求。备份及验证记录：`/var/lib/aiqq/backups/gpt-image-502-retry-20260915T105729`。

## 2026-09-16: Complete QQ expiry variants (R10)

- [x] Correlate real failure with request627 and reproduce offline; preserve exact pre-change files.
- [x] Implement both SDK expiry variants and foreground delivery exception containment.
- [x] Targeted regressions, independent review, full packaged suite and diff/syntax checks.
- [x] Restart after checking active work; verify QQ, local/public health and NovelAI readiness.
- [x] Record deployment and exact rollback delta without sending QQ test messages.

R10 deployed 2026-09-16 11:14:38 +08:00, PID1192195; 35 targeted / 212 full tests passed, independent review clear. Local/public HTTP200, QQ connected, NovelAI configured/ready. See `research/qq-expiry-variants.md`.

## R11 planning status — no implementation started

PRD/design/checklist v4 in `research/chat-vision-plan/` cover complete recent50 messages, skill/read tools and raw event/version/attachment metadata persistence. User explicitly removed original-image storage, persistent cache and prefetch; only per-request temporary downloads, cleaned after use, with resend guidance for expired/unavailable links. Earlier24h/1GiB storage proposal is superseded. No application/config/skill edits, database migration, model calls or restart authorized in this planning round; R12 remains deployed.

## R12: Pure-image delivery

- [x] Record user requirement and preserve exact five-file baseline.
- [x] Correct image fallback payload and update existing regression assertions.
- [x] Independent review, full packaged tests and diff/syntax checks.
- [x] Restart after checking activity; verify QQ, local/public health and NovelAI.
- [x] Record exact deployment delta and result.

R12 deployed2026-09-16 14:16:21 +08:00, PID1251315, after request763 completed. 22 targeted /212 full tests passed; reviewed five-file hashes unchanged. Local/public HTTP200, QQ connected, DB open and NovelAI configured/ready. Image messages contain no mention caption, including expiry fallback; text/Markdown retain @. No manual QQ send; see `research/qq-image-only-delivery.md`.
