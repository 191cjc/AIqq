# 派大星换画风成功记录复核

复核日期：2026-09-15。以下时间均为 Asia/Shanghai。

## 成功确实发生

只读查询 `/var/lib/aiqq/group_messages.db`，结合 `journalctl -u aiqq.service`：

| 2026-09-14 时间 | 证据 | 结果 |
| --- | --- | --- |
| 14:47:56 | 群消息记录 146 | 发送网页检索得到的原始派大星表情包。 |
| 14:48:22 | 群消息记录 147 | 用户要求“将他改为jojo画风”。 |
| 14:50:16 | PID 516581 的 HTTP 日志 | `POST https://api.airoo.cc/v1/images/edits` 返回 502。 |
| 14:50:16 | 同进程 SDK 日志 | `Retrying request to /images/edits in 0.487837 seconds`。 |
| 14:50:55 | 同进程 HTTP 日志 | 同一编辑接口返回 200。 |
| 14:51:18 | 群消息记录 148 | 回复将派大星“我爱你”改为 JOJO 漫画画风。 |
| 14:51:21 | 群消息记录 149 | 发出图片，message_type=7，含一个附件和 media。 |
| 15:04:38 | 群消息记录 150 | 用户继续要求日本 Galgame / 柚子社画风。 |
| 15:06:32 | PID 516581 的 HTTP 日志 | 同一编辑接口再次返回 200。 |
| 15:06:43 | 群消息记录 152 | 第二次发出图片，message_type=7，含一个附件和 media。 |
| 16:37:58 | PID 551714 的 HTTP 日志 | `/images/generations` 返回 404。 |
| 16:56:35 | 同进程 HTTP 日志 | `/images/edits` 返回 404。 |

因此，换画风不是只有口头承诺，也不是把最初的网页找图误当作 GPT 改图成功。HTTP 200 后有实际图片发送记录。第一次成功包含一次 502 后的自动重试。

## 本地代码和配置对照

历史开发会话 `01a0859e-16d7-7f52-9588-2959d17f8eae` 的原始本地记录提供了两项补充证据：

1. 2026-09-10 10:34:49 的图片客户端源码快照（会话 JSONL 第 1075 行）共 168 行，与本次修改前的私有备份 `src/aiqq/services/images/gpt.py` 逐字一致。已经使用 multipart `images.edit`、`gpt-image-2` 默认模型、`max_retries=2`，并传递 `response_format="b64_json"`。在已检查会话 9 月 14 日 14:35 至 16:19 的修改记录中，没有发现该图片客户端改动。
2. `.env` 的 16:18:31 修改（同会话第 3450 行）是单行补丁：`OPENAI_MODEL=gpt-5.6-sol` → `OPENAI_MODEL=gpt-6-astra`。16:18:40 随后重启。该补丁没有修改图片密钥、图片地址或图片模型。历史和当前配置代码均单独读取 `OPENAI_IMAGE_MODEL`，默认 `gpt-image-2`；bootstrap 将 `config.images.model` 传给图片服务。

这些记录支持成功前后使用同一图片调用实现。没有完整的历史进程环境或图片密钥指纹，不能声称已经证明每个运行参数绝对一致；也不能根据 `.env` 修改时间推断图片密钥发生过变更。

## 修正后的归因

- 接口路径和 multipart 改图调用本身有成功案例；不能把后来的 404 解释为该方式从来不受支持。
- `response_format` 不符合官方 GPT 图片请求契约，删除仍是合理的兼容性修正。但早期相同实现能成功，所以不能把该参数认定为此次先成功后失败的根因。
- 当前代理的明确错误正文是 `Model "gpt-image-2" is not supported by any configured account in this group`。9 月 14 日 18:09:46 已用删除该参数后的有效请求复现。它描述的是该次请求的模型/分组可用性，不代表历史上始终不可用。
- 历史两次 404 没有保存正文；它们是否与后来的明确模型分组错误完全相同，仍需代理侧请求日志确认。代理账号上下线、路由/分组调整或账号状态变化都尚未被证实。
- 16:18 聊天模型切换不会直接改变 Images API 的 model 参数。没有证据表明切换聊天模型本身造成此次 404。

## 重试策略影响

本次已部署代码把 SDK `max_retries` 从 2 改为 0，这是避免图片 POST 隐式重放的策略选择，不是修复 404 的手段。历史成功证明自动重试确实曾从 502 恢复；当前设置失去了这项恢复能力。是否恢复有限重试，应与代理的重复请求/计费语义一起单独评估，不能再把关闭重试笼统描述为提高成功率。

## 本次操作边界

本次复核只读取数据库、系统日志、代码、会话历史及服务状态，并补充任务文档。没有调用真实生图、发送 QQ 消息或再次重启。复核时服务仍为 active/running，PID 593585，NRestarts=0。图片能力尚未验收恢复。

## 用户授权后：原实现真实改图复测

2026-09-15 10:52:34 至 10:52:36，用户要求测试原接口目前是否正常。独立加载修复前备份的 `GPTImageService`，使用当前图片配置和原始请求参数，包括 `response_format=b64_json`、multipart 文件上传与 SDK `max_retries=2`。参考图为本地生成的白底蓝色圆形 PNG，要求将圆形改成绿色；没有使用群聊图片或向 QQ 发送消息。

- 实际接口：`POST https://api.airoo.cc/v1/images/edits`。
- 模型：`gpt-image-2`。
- 结果：约 1.49 秒后 HTTP 404，`provider_type=model_not_found`。
- 错误摘要：`Configured model is not supported by any configured account in this group`。
- Provider request ID：`fbdd67ea-42fe-4176-bf30-18a42e2c9efa`。
- 实际只发出一次请求：SDK 默认不会因本次 404 自动重试，原有重试设置仍保留。
- 结论：在当前图片配置下，原实现也无法完成改图；恢复旧参数或旧重试次数不能解决此次模型分组 404。
- 脱敏结果及测试参考图：`/var/lib/aiqq/backups/original-image-edit-check-20260915T105234+0800/`。
- 没有修改生产代码、配置或重启服务。没有生成结果图片。
