# 图生图此前如何取得图片地址

只读代码核对，2026-09-16。下面描述现有服务行为，不是v2方案已实施。

1. QQ网关收到图片消息时，`GroupMessageRepository.add_gateway_event`保存完整data到payload_json，其中含attachments及可能嵌套的msg_elements。消息获得本地record_id。
2. 普通聊天模型只看到历史中的record_id、has_image和文字等少量字段。`ChatAgent.CHAT_INSTRUCTIONS`要求明确改图请求输出image_action.mode=edit，并选择真实source_record_id；`schemas.py:225`按输入中允许的图片记录集合校验。
3. `ConversationWorkflow._execute_image_action`（`src/aiqq/logic/conversation.py:226`）在图片提示词审核后调用`reference_image_loader.load(group_id, source_record_id)`。这里group_id来自当前请求，不由模型选择。
4. `GroupReferenceImageLoader.load`调用`GroupMessageRepository.list_image_urls`。后者先通过`get_by_record_id`查询当前群且未撤回的消息，再从其payload提取图片URL。
5. `_extract_image_urls`（`src/aiqq/database/group_messages.py:423`）遍历attachments/msg_elements，收集content_type以image/开头且有URL的附件，去重，最多10个候选。
6. `GroupReferenceImageLoader`依次用`WebImageService.download`下载候选，返回第一个有效ImageAsset；下载器检查HTTPS、公网解析、跳转、大小、真实图片格式等。全部失败抛类型化原图错误，原图失败不预留生图额度。
7. 图片适配器`CodexResponsesImageService`通过`backend.run(..., input_images=(reference_image,))`把下载好的字节交给独立生图Codex。SDK将其暂存为文件，并构造local_image输入；本地桥接再向Responses发送带image_generation工具的请求。

因此，聊天模型选择的是消息编号；应用程序从已保存QQ事件中取得URL并下载；真正的参考图输入由独立生图后端接收。聊天模型没有因为做过改图就自动获得该图片的视觉内容。

可复用：同群记录定位、附件URL提取、下载校验、ImageAsset和错误分类。新识图功能需要把这些结果接入普通聊天的图片查看/输入通道。

记录页能显示图片是另一条流程：`interfaces/web/group_messages.py:391`直接将payload.attachments.url生成HTML img标签，由浏览器读取。它不证明聊天模型获得了URL或像素。
