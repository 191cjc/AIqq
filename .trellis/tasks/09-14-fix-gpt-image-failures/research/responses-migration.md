# Responses 生图迁移（2026-09-15）

用户先提出 gpt-5.6-sol + Responses image_generation；实测其 Codex 通道走 Responses Lite，返回 400：不支持 top-level image_generation。用户随后明确接受改用成功过的 gpt-6-astra。

## 实测契约

- 工具名为 `image_generation`；图片结果在 `image_generation_call.result` 的 Base64 中。
- 当前图片密钥直接通过普通 HTTP 调用 /responses 返回 403：`This account only allows Codex official clients`。不能只改地址，不能以伪造客户端头作为接入方案。
- 使用项目实际 Codex Python SDK/CLI 发起隔离请求，在本地受控传输中声明图片工具，保留真实 SDK 请求头，即可通过验证。
- gpt-5.6-sol 实际带 x-openai-internal-codex-responses-lite=true；400 请求 ID `6d83b11a-7c44-4583-b759-7c74b7d969f2`。
- gpt-6-astra 使用当前图片密钥，2026-09-15 15:03:57 开始，约 38.99 秒成功，HTTP 200 SSE，终态 response.completed；返回 1254×1254 JPEG（48112 字节）。请求 ID `806945f9-ed70-4bfc-820e-9e31220258c1`。记录：`/var/lib/aiqq/backups/astra-codex-image-check-20260915T150357/result.json`。
- 工具参数：model=gpt-image-2、size=1024x1024、quality=medium、output_format=jpeg、output_compression=85。代理返回尺寸可能不同，保留已有 1024×1024 JPEG 规范化。
- 参考图编辑亦成功：2026-09-15 15:05:37 开始，约 45 秒，HTTP 200、response.completed、SDK 完成；JPEG 1254×1254（47700 字节），请求 ID `0cf3e4b8-27cc-4e40-9692-cd2d4bf2d15c`。记录：`/var/lib/aiqq/backups/astra-codex-edit-check-20260915T150537/result.json`。
- 官方文档页此次仍返回 403；工具字段以已安装 OpenAI 官方 SDK 的类型定义和实际响应验证为依据，不声称在线模型文档已读。

## 最小接入边界

新增图片专用 CodexResponsesImageService，保持原 GeneratedImageService 的 generate(action, reference_image=...) 和 close() 接口。启动装配改用它；旧 Images API 模块只复用图片规范化、安全错误分类和诊断，不作自动降级调用。

图片专用适配器通过临时 loopback HTTP 端点接收真实 Codex SDK 的 Responses 请求：仅处理固定模型、固定路径、带本次临时令牌的请求；由服务持有的图片 API key 发往配置的固定上游 /responses。实际 SDK 请求头保留，不伪造 Codex 客户端身份。替换 tools 为唯一的 image_generation 声明、强制该工具，保留 SDK 的输入图片编码。图片专用 SDK 运行关闭自身重试，由传输桥仅对开始响应时的 HTTP 502 重试两次（0.5/1 秒）。一次业务调用只接受一轮 SDK 上游请求，避免 SDK 再次 POST 重复生成；多余请求在本地拒绝。

SSE 解析必须有大小上限、正确处理分块 UTF-8 和事件边界；只在 response.completed 且图片调用完成、结果有效时成功。HTTP 200、工具开始事件或只有文字都不是生图成功。流中断、错误终态、无图、无效图片不重试。模型文本和原始响应不能进入日志。取消/超时/关闭要终止 SDK 和本地桥，并释放原有额度占用。

CodexSDKBackend 仅增加默认关闭的图片专用运行开关：该模式允许原生图片工具、关闭网页工具和 SDK 通用重试，普通聊天默认行为保持原样。直接使用用户指定的 gpt-6-astra 驱动，工具模型仍为 OPENAI_IMAGE_MODEL（gpt-image-2）。新增 OPENAI_IMAGE_DRIVER_MODEL 配置，默认 gpt-6-astra。

父代理负责配置、bootstrap、README、spec 和部署；实现子代理负责新图片适配器、SDK 图片专用模式与对应服务测试。业务审核、额度、群历史策略不变，10000 字符配置保留。验证两入口、重试/取消/安全诊断以及生成和参考图编辑的真实图片后部署重启；QQ 消息仍须由用户发起，不代发。
