# 故障证据与代码边界

检查日期：2026-09-14，时区 Asia/Shanghai。

## 已检查

- 运行入口：`systemd aiqq.service` → `.venv/bin/python -m aiqq.main`，进程在 16:18:40 启动。
- 最近失败来自新包 `aiqq.services.images.gpt`，不是根目录旧实现。
- 文生图 404：2026-09-14 16:37:58，provider request ID `fd53d32b-eb5f-4807-8828-a4a858b193b2`。
- 改图 404：2026-09-14 16:56:35，provider request ID `b922252f-38f2-43f0-8298-e386b7f320c6`。
- 此前改图 14:50:16 返回 502，SDK 自动重试后在 14:50:55 返回 200；15:06:32 再次返回 200。
- 最近两次失败前都通过了审核和本地额度判断；改图也已成功取得参考图片。失败发生在 QQ 图片上传之前。
- 16:47:51 的公开网页图片曾成功发送到 QQ。网页找图与 GPT 生成使用不同服务。
- 旧媒体链接的 HTTP 404 属于临时图片过期等展示问题，与这两次 Images API POST 的 404 是不同请求，不能混为一谈。

## 配置核对

- `OPENAI_IMAGE_BASE_URL` 空，继承 `OPENAI_BASE_URL=https://api.airoo.cc/v1`。
- `OPENAI_IMAGE_MODEL` 空，默认 `gpt-image-2`；使用独立图片密钥。
- `.env` 修改时间为 16:18:31，早于当前服务启动；仍应在实施时核对真实生效配置，而不是在日志中输出整个配置对象。
- 使用图片密钥查询 `/models`：HTTP 200，标准 data 数组有 12 项，无图片模型。这不证明 Images API 不支持目标模型，也不证明 POST 会被同样授权。

## 文件定位

| 文件 | 现状或可复用模式 |
| --- | --- |
| `src/aiqq/services/images/gpt.py:53` | 创建 SDK 客户端并启用两次自动重试。 |
| `src/aiqq/services/images/gpt.py:73`、`:90` | 两个真实图片 POST；当前都有 `response_format="b64_json"`。 |
| `gpt_image_service.py:146`、`:179` | 旧调用未显式传 response_format；最新 SDK 参数定义确认 GPT 图片不支持该字段，但它不能解释省略该字段时仍出现的模型分组 404。 |
| `src/aiqq/services/images/gpt.py:103` | 错误正文未进入日志；旧版的错误摘要提取可作为设计参考。 |
| `src/aiqq/exceptions.py` | 框架无关的公共异常位置。 |
| `src/aiqq/logic/image_generation.py:59` | 显式 GPT 命令把所有生成失败统一处理。 |
| `src/aiqq/logic/conversation.py:145`、`:188` | 上下文生图及参考图下载、额度、生成的编排。 |
| `src/aiqq/logic/image_quota.py` | 当前在成功生成后计数，finally 路径释放占用；需覆盖失败/取消回归。 |
| `tests/unit/services/test_gpt_image.py` | 目前主要使用 FakeImages，缺少真实 SDK + mock HTTP 的失败契约测试。 |

## 后续核对

随后使用当前图片密钥取得 `model_not_found` 正文：`Model "gpt-image-2" is not supported by any configured account in this group`。当前问题已收敛到代理分组的图片账号/模型配置；旧请求的原始正文仍无法追溯。参数契约及线上探测边界见 `research/gpt-image-contract.md`。未进行真实生成或配置变更。

## 2026-09-15 历史成功复核

派大星 JOJO 换画风与随后 Galgame 换画风均有 Images API 200 和实际 QQ 图片发送记录。早期图片客户端源码与修复前备份一致，已经传递 response_format；16:18 的配置补丁仅切换聊天模型，没有修改图片配置。因此不能将 response_format 或聊天模型切换认定为先成功后失败的根因。当前模型分组错误也不能外推为历史上一直不可用。完整时间线、历史源码对比及关闭重试的影响见 `research/historical-success.md`。
