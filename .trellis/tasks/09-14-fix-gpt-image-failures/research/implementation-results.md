# 实施与重启结果（2026-09-14）

## 已部署

- 移除 GPT 生成和编辑的 response_format，保留 JPEG 输出、压缩、尺寸、质量和高保真编辑参数。
- 生产创建的图片客户端 max_retries=0；记录开始/结束、耗时、尝试 ID、HTTP 状态、上游请求 ID、安全错误类型和已知错误摘要。
- 引入公共图片失败类型；普通对话生图与显式命令返回一致的固定错误文案和分类。
- 覆盖失败/取消的本地额度与全局占用释放；未修改业务数据库、密钥或代理地址。
- 更新 README 与 `.trellis/spec/backend/image-services.md`。

## 验证

- 图片适配器：12 项 unittest 方法，包含 generate/edit 的 HTTP 参数、22 个状态组合、8 个网络错误组合，以及脱敏、无效响应、取消等用例。
- 完整新架构单元测试：133 项通过。
- git diff --check 与相对私有备份的本次文件空白检查通过。
- 项目没有配置 linter 或 type checker，不声称已执行这些工具。
- 交叉检查没有发现阻塞当前生产部署的问题。显式外部注入 SDK 客户端仍沿用调用方的 retry 设置；当前 bootstrap 自建客户端不受影响，生产单次 POST 有测试覆盖。

## 真实接口

修正后只执行一次文生图，耗时 1566ms，返回 404：

- `error_kind=not_found`
- `provider_type=model_not_found`
- `provider_message=Configured model is not supported by any configured account in this group`
- `attempt_id=8b4f3d07545a4b878f4662545f2b88c0`
- `provider_request_id=839821e4-c743-468e-8c29-e7cbf7ee4e0b`

失败后停止本批次，未调用改图，未代发 QQ 消息。当前工程配置只有 API 调用密钥，未找到代理后台管理凭据，本机也未发现该代理的管理服务配置。代理分组需要可承接 gpt-image-2 的账号/模型通道，或者该服务已开通图片能力的密钥。

## 服务重启

- 用户已明确授权重启。
- `sudo -n systemctl restart aiqq.service` 执行成功。
- 新进程 PID：593585；启动时间：2026-09-14 18:14:19 +08:00。
- systemd：active/running，NRestarts=0。
- QQ 网关于 18:14:21 连接，18:14:22 完成 ready。
- 本地与公网 `/health` 都返回 HTTP 200，QQ connected=true，群消息数据库已打开。

## 交付状态与恢复点

代码修复和服务重启已完成，图片通道恢复仍未完成；真实改图和 QQ 图片交付尚待代理配置恢复后验收。任务保持 in_progress，不归档、不标记完整恢复。

本次修改前的八个代码/测试/README 文件、部署 SHA-256、真实接口结果和重启后健康记录保存在私有目录：
`/var/lib/aiqq/backups/gpt-image-fix-20260914T100207Z`。

回滚使用该目录中的本次文件基线，避免影响原有其他未提交改动。现有密钥未被复制到结果文档或日志。

## 2026-09-15：恢复有限 502 重试并部署

- 用户要求恢复偶发 502 自动重试。图片适配器仅在 HTTP 502 后等待 0.5 秒、1 秒，最多重试两次；SDK 通用自动重试仍关闭，404、429、其他 5xx、超时、断连及无效图片响应不重试。
- 同一模型、提示词和参考图重放；增加安全的 `gpt_image_retry` 事件，保留单次开始/结束日志、取消传播和最终响应的准确诊断。
- 18 项图片服务定向测试和 139 项完整新架构测试通过；独立审查通过，未发现阻塞问题。
- 2026-09-15 11:03:45 +08:00 重启成功，PID 828706，active/running，NRestarts=0。本地及公网健康检查 HTTP 200，QQ 已连接，数据库已打开。
- 本轮没有付费图片请求或 QQ 消息发送。最近真实接口检查仍为 10:52 的原实现改图 404；不能以重试部署成功认定模型分组问题已恢复。
- 本轮精确文件备份、验证记录、部署哈希及健康结果保存在 `/var/lib/aiqq/backups/gpt-image-502-retry-20260915T105729`。
