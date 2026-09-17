# 消息信息完整性审计与方案补充

> 最新方案更正：用户已明确不保存图片原文件；以下原图缓存/存储讨论属于当时的诊断或历史方案。当前仅持久保存图片链接和元数据，识图时临时下载，确认链接过期/失效后提示重新发送。以prd.md、design.md和storage-design.md的v4范围为准。

2026-09-16，仅只读排查和方案更新。用户最新要求是完整传递消息记录里的信息，避免今后新增字段再次遗漏。完整性基准改为数据库记录及其原始payload，不以网页渲染结果或现有15字段DTO为上限。

## 当前链路中的取舍

| 环节 | 已确认行为 | 影响/方案处理 |
| --- | --- | --- |
| 网关持久化 | gateway.py和add_gateway_event只接收GROUP_MESSAGE_CREATE/GROUP_AT_MESSAGE_CREATE，要求消息和群ID；将完整d存入payload_json，事件ID/类型另存 | d内未知字段目前会保存；网关外层op/s等未完整归档。此次完整性承诺针对已保存消息，不声称历史未存字段可恢复。 |
| 同消息更新 | _upsert按message_id覆盖已有字段和整个payload，不保留各次事件版本 | 完整传递最终已存版本不等于重建所有原始事件版本；本轮不扩大为网关审计存档改造。 |
| 机器人记录 | sender.py组装业务payload，含markdown、keyboard、media、来源和交付状态等 | 这些已保存内容应全部传入；不声称记录是QQ发送HTTP响应的完整副本。 |
| 数据行解析 | _row_to_message只构造StoredGroupMessage已声明字段；无效或非对象payload_json退化为{} | 当前库15列均已覆盖，但以后新增列会在固定DTO丢失；新通路从整行映射保留所有列，解析失败保留原文与错误标记。 |
| 历史查询 | 只选GROUP_MESSAGE_CREATE/GROUP_AT_MESSAGE_CREATE/BOT_MESSAGE_CREATE；排除recalled_at非空；最多50条并排除当前请求及之后的记录 | 进度消息和撤回记录在记录页可查，却不进入聊天。新方案按同群记录页的原始记录口径取最近50条，保留事件类型及撤回状态；当前请求独立完整提供。 |
| 历史投影 | _to_history_message / _history_message_to_wire仅保留8字段 | 附件、mentions、发送者详情、原始引用、机器人扩展信息被省略；取消消息字段白名单。 |
| 引用 | _extract_reply_summary最多取3个元素的非空content并拼接 | 嵌套引用结构、其他元素字段和后续元素没有进入模型；全文payload传递后摘要只能辅助。 |
| 正文 | prepare_group_context压平空白，按10000字符预算截断或丢弃更老记录 | 取消这两项转换，保留原文及换行；实际上下文超限必须明确，不能静默剪切。 |
| 当前请求 | _incoming只保留4字段，后续只将clean_prompt后的user_input交给模型 | 附件、引用、mentions和当前发送者等缺失；新current_message从原始已存记录取得，清理后的user_input仅辅助。 |
| JSON提交 | ChatAgent完成JSON后，SDK/Responses没有再按消息字段进行二次筛选 | 主字段缺口在上游投影；输出schema的严格校验不等于输入字段过滤。 |
| 图片消费 | URL提取最多10个候选、加载第一个有效候选；模型后端images[:1] | 这些是图片读取边界，不该裁剪输入里的附件列表；全部附件元数据保留，实际识图数量单独明确限制。 |
| 记录页面 | 50条一页、最多20个附件、最多50段元素文字、递归4层；正文strip后渲染 | 网页也不是完整原始记录；不能通过抓网页构建“完整输入”。页面限制不会删掉数据库payload。 |

## 已存而未传给聊天模型的实际字段

只读检查当前数据库最近50条记录的键名，未输出群/用户ID、消息正文或签名URL：

- author含id、member_openid、member_role、union_openid、username、bot。
- mentions含id、is_you、member_openid、member_role、scope、username、bot。
- 消息含group_id/group_openid、message_scene、attachments及其他原始字段。
- 机器人记录含markdown、keyboard、media、origin、delivery_mode、source_message_id、fallback_reason、msg_seq。
- attachments还出现content、filename、content_type、height、width、size、url。
- 样本含1条BOT_PROGRESS_MESSAGE、1条带撤回状态的记录；检查只说明该全局最近50条样本，不等同某个具体请求的同群50条窗口。
- 当前表15列，无额外未知列；样本payload均可解析。这不构成未来字段不会丢失的保证。

## 完整传递契约（方案）

1. 模型输入从同群数据库原始记录整行构建，不经固定字段的StoredGroupMessage/GroupHistoryMessage做有损投影。采用可扩展JSON映射，新增列与未知字段自动保留。
2. 唯一约定的表示转换是payload_json解析为payload，要求JSON语义等价；数据库其他列保留原值，不把未知字段或空值删除。若字段命名冲突，保留源字段并显式处理，禁止覆盖。原字符串解码失败或根类型异常时，保留payload_json原文和错误标记，不能默认为空对象并宣称完整。
3. payload的每个键和值、未知对象、嵌套数组、数组顺序、null/false/0/空字符串均保留。附件全列表和引用全结构保留；新出现的视频/文件等字段先完整传递元数据，不因此承诺新增模态处理能力。
4. current_message、首轮最近50条、更早历史查询使用同一个完整序列化入口。辅助的role/has_image/引用索引/缓存状态单独存放，不能替代或覆盖原始字段。
5. 记录选择以同群记录页的原始数据范围为准：当前请求之前最近50条已保存记录，包含进度消息和撤回记录，原样携带event_type/recalled_at，不隐藏整条记录。撤回标记必须告知模型，不能把它当成仍有效的新指令；图片读取服务继续拒绝主动取已撤回原图，不因元数据完整而重发消息。
6. 不按10000字符裁剪、压平空白、截断附件数组或引用层级；不从网页渲染结果抽取记录。确实超过上游上下文时明确报告并缩小范围，不能标记完整却静默删除数据。
7. 验收使用字段/值的完整性校验：当前和历史都加入未知顶层列、未知payload键、深层引用、超过20个附件等样本，确认序列化后仍全部存在；JSON键顺序无需一致，数组顺序和标量值必须一致。此处是未来实施验收，尚未添加产品测试。

## 范围解释

本轮扩大的是完整字段与记录页口径的一致性；仍限定当前群、首轮50条、更早按需。不是获取其他群、未下发消息、未保存的旧事件版本或服务环境配置。实际图片像素仍通过读取工具/图片输入交付，不能把完整URL当作已经看过图片。

状态：只改方案。没有运行代码、skill、配置、服务变更，没有付费调用或QQ发送。

## 入库边界的进一步核对

全库785条的只读核对、机器人图片元数据缺项、同消息覆盖和撤回状态边界见 [database-retention-audit.md](database-retention-audit.md)。已撤回记录的完整传递只覆盖库里已有标记，不能代表已捕获所有群成员撤回。

## 原始存储扩展已纳入方案

随后按用户要求，完整网关原文、消息版本、生命周期状态及原图/元数据的保存设计已补充至 [storage-design.md](storage-design.md)。此前审计描述现状，不再代表这些缺项被排除在计划之外。
