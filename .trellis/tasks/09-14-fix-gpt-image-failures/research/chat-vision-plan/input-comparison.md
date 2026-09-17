# 聊天模型输入前后对比（仅方案示例）

2026-09-16。两个输入使用同一组虚构/脱敏数据：成员甲发送表情包501，成员乙在消息502询问其含义。仅展示1条历史以便阅读；这不改变实际方案中最近50条的范围。URL使用不可用的example.invalid占位域名，身份/消息编号均不是本次线上请求证据。

“修改前”通过现有_to_history_message、prepare_group_context(char_limit=10000)和真实ChatAgent.run离线捕获。替代后端只收集参数并抛固定异常，没有模型/网络调用。“修改后”为方案数据契约示例，不是已实现序列化器的输出；protocol_version=3、新增字段命名和示例工具返回结构仍属建议。

最新补充：本例展示当时15字段的静态样例，不是未来的字段白名单。正式方案要求数据库新增列和未知payload自动保留，进度/撤回记录与记录页口径一致，详见 [完整性审计](message-completeness-audit.md)。

## 字段与行为

| 对比项 | 当前实际输入 | 方案输入 |
| --- | --- | --- |
| 首轮历史范围 | 最多50条，再按正文/引用总计10000字符裁剪，可能少于50条 | 最近50条全文与完整字段，char_limit=null；极端上下文超限需明示 |
| 正文 | 规范化空白，预算不足时截断 | 保留原文和换行 |
| 单条记录 | record_id、role、sender_name、sent_at、content、message_type、reply_summary、has_image | 保留数据库整行及payload，当前15列与今后新增列均传递；派生信息另附 |
| 发送者 | 显示名称及user/assistant角色 | 另有成员标识、群角色和机器人状态，能区别同名成员 |
| 图片 | has_image布尔值，无URL和附件数据 | 原始payload.attachments包含URL、MIME、尺寸等实际已有字段 |
| 引用与表情 | 原始正文标记和最多3条元素文字拼接摘要 | 保留完整msg_elements及已有嵌套信息，可靠关联单独标记；不能根据不明msg_idx伪造原消息ID |
| 当前消息 | 清理@后的user_input文字 | user_input及完整current_message，当前附件不会被历史查询排除 |
| 历史范围说明 | available与消息列表 | 增加条数、首尾记录、has_older_messages和content_complete |
| 更早记录 | 普通聊天没有读取接口 | skill+脚本按需分页，返回完整字段和游标 |
| 视觉内容 | input_images未传，实际0张 | 先标记not_loaded，实际读取后用view_image或input_images交给模型 |

## 当前单条消息：真实序列化形状

```json
{
  "record_id": 501,
  "role": "user",
  "sender_name": "成员甲",
  "sent_at": "2026-09-16T11:43:48+08:00",
  "content": "这张表情包太有意思了 <faceType=6,faceId=\"0\",ext=\"eyJ0ZXh0IjoiIn0=\">",
  "message_type": 0,
  "reply_summary": "",
  "has_image": true
}
```

## 方案中的同一条消息：完整记录形状

```json
{
  "record_id": 501,
  "message_id": "message_demo_501",
  "event_id": "event_demo_501",
  "event_type": "GROUP_MESSAGE_CREATE",
  "group_openid": "group_demo",
  "member_openid": "member_demo_a",
  "username": "成员甲",
  "member_role": "member",
  "is_bot": 0,
  "content": "这张表情包太有意思了\n<faceType=6,faceId=\"0\",ext=\"eyJ0ZXh0IjoiIn0=\">",
  "message_type": 0,
  "sent_at": "2026-09-16T11:43:48+08:00",
  "received_at": "2026-09-16T03:43:48.500+00:00",
  "payload": {
    "id": "message_demo_501",
    "group_openid": "group_demo",
    "author": {
      "member_openid": "member_demo_a",
      "username": "成员甲",
      "member_role": "member"
    },
    "content": "这张表情包太有意思了\n<faceType=6,faceId=\"0\",ext=\"eyJ0ZXh0IjoiIn0=\">",
    "message_type": 0,
    "timestamp": "2026-09-16T11:43:48+08:00",
    "attachments": [
      {
        "url": "https://example.invalid/meme.jpg?signature=PLACEHOLDER",
        "filename": "meme.jpg",
        "content_type": "image/jpeg",
        "width": 1079,
        "height": 1081,
        "size": 65831
      }
    ],
    "msg_elements": []
  },
  "recalled_at": ""
}
```

原始payload保留QQ实际收到的字段；示例没有引用，所以msg_elements为空。附件的size、width等只在QQ实际提供时保留，不凭空补齐。外层记录按数据库原值映射（例如is_bot保留0/1），payload是原始事件对象，两者有自然重合；不要为了消除重合丢掉原始字段。

## 整个请求与图片读取

完整JSON可查看：

- [修改前完整请求](examples/input-before.json)
- [修改后完整请求（方案）](examples/input-after-proposed.json)
- [图片读取返回（方案）](examples/image-read-result-proposed.json)

方案信封在现有operation/user_input/reference_material基础上增加current_message、历史范围和visual_context。例中图片处于not_loaded，只有读取脚本下载/缓存成功并通过图片查看工具或实际图片输入传给模型后，才能认为已提供像素。

```text
完整历史记录501（含附件URL）
  → read_image.py --record-id 501 --attachment-index 0
  → 本地图片文件与来源信息
  → view_image实际查看，或input_images实际附图
  → 回答表情包含义
```

不把base64像素塞进文字记录。脚本返回本地路径并不等于模型已看图。完整URL和记录字段解决定位，视觉输入解决内容理解。

## 本轮验证与状态

离线捕获确认当前图片数0，当前正文换行被折叠；方案示例与源记录全部15个字段对应，正文及payload保留。未调用任何模型、发送QQ消息或修改代码/配置/服务。所有after文件是可审阅的示例，非上线结果。
