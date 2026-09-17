# R11 按需读取服务、脚本与解码实施记录

2026-09-16。用户“开始改动”授权后的实现；未调用付费模型、未发送 QQ 消息、未修改生产数据库或重启服务。

## 文件与接口

- `src/aiqq/logic/chat_read.py`：`ChatReadSession`、`ChatReadServiceProtocol`。会话提供 `read_history(**filters)`、`read_image(record_id, attachment_index=0, version_id=None)`、`close()`，以及同群已读取记录／可用图片记录编号集合。图片返回 `(metadata_dict, tuple[ImageAsset, ...])`，不含宿主文件路径。
- `src/aiqq/services/chat_read.py`：`GroupChatReadService(repository=..., downloader=...).create_session(group_id)` 将群范围绑定在服务端。历史返回存储层完整分页结果；不折叠文字、不截断字段。当前、历史和指定版本图片通过同群附件关联定位，不接受任意 URL。历史检索获得的图片编号可供已有改图动作选择。
- `src/aiqq/services/images/chat_decode.py`：只用于输入识图的 JPEG/PNG/WebP/GIF 解码。动画采样首、中、尾帧，输出普通 JPEG `ImageAsset` 与来源格式、尺寸、字节数、SHA-256、帧索引／时间等实测元数据。生成结果的静态校验规则保持不变。
- `src/aiqq/services/images/web.py`：下载字节校验提取为共用入口，`download_for_read()` 复用已有 HTTPS、公网 DNS、重定向、字节量和超时规则；解码使用工作线程。既有 `download()` 返回行为不变。
- `.agents/skills/aiqq-chat-read/`：新增 `SKILL.md`、`scripts/read_image.py`、`scripts/read_history.py`、共享 `_bridge.py`。脚本仅使用 Python 标准库，以本轮 URL 和短期凭据请求 `/image` 或 `/history`；只允许 `http://127.0.0.1:<port>`，禁用代理和重定向。运行时另负责本地桥接、临时文件、工具调用和隔离。
- `tests/unit/services/test_chat_read.py`：读取服务、解码、真实脚本 HTTP 请求和 SQLite 存储联调回归。

## 边界与资源上限

每请求最多 3 个图片来源，每来源最多 3 帧，合计最多 9 个视觉输入；同请求重复来源共享下载和失败结果，结束后清除。历史最多 3 次查询，每次最多 50 条，预算耗尽返回已查范围；消息、版本、原始事件分别使用存储层游标。

下载沿用 20 MiB／20 秒默认上限；帧像素沿用 4096×4096 上限。动画最多解析 120 帧、累计最多 64×1024×1024 像素，每帧归一为 JPEG。下载与解码最多 4 个并发。脚本完整响应超过 64 MiB 时明确返回容量错误，绝不输出截断的记录。

收到图片不会预取。服务只在内存中临时持有下载／解码结果，不新建原图文件、缓存或 blob 索引。记录实测元数据和最近读取结果；失败保留既有测量数据和原 URL。运行时负责本轮像素文件的写入及请求结束／失败／取消／崩溃残留清理。

撤回状态保持当前视图的单调确认，旧版本也不能绕过撤回限制。已撤回和跨群记录不下载。仅明确 404／410 分类为过期或失效，固定提示“这张图片链接已过期或失效，请重新发送图片。”未知 400、权限、超时、无效图片和资源超限各自保留原因。没有像素时返回 `pixels_available=false`；成功提供像素仍要求运行时实际调用查看工具后才描述内容。

## 已运行验证

```text
.venv/bin/python -m unittest tests.unit.services.test_chat_read tests.unit.services.test_web_image tests.unit.services.test_reference_image -q
Ran 32 tests — OK

.venv/bin/python /home/ubuntu/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/aiqq-chat-read
Skill is valid!

git diff --check
无输出，通过
```

覆盖实际 GIF／动态 WebP 三帧像素颜色及顺序、静态格式、动画／像素预算、普通生成校验仍拒绝 GIF、既有下载错误与地址限制、历史全文超过 10000 字符和未知空值、每轮查询及来源预算、同请求复用／跨请求不缓存、撤回／跨群拒绝、取消后无后续测量写入、真实脚本授权和完整 JSON 往返，以及临时 SQLite 中网关事件→附件版本→图片读取→实测元数据往返。SQLite 联调目录仅出现数据库及其 WAL 文件，没有图片原文件。

本记录不代表真实代理看图验证、运行时隔离审查或部署已完成；这些由主任务后续合并验收记录。
