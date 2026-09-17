# GPT Image 调用方式核对（2026-09-14）

## 当前 404 已取得明确错误正文

向实际配置的 `https://api.airoo.cc/v1/images/generations` 提交缺少必填 prompt 的诊断请求，明确指定 `model=gpt-image-2`，返回：

```json
{
  "error": {
    "message": "Model \"gpt-image-2\" is not supported by any configured account in this group",
    "type": "model_not_found"
  }
}
```

HTTP 404，provider request ID `e5697d63-a021-4054-8811-f311d07c76ff`。先前空 JSON 探测也得到相同模型分组错误，request ID `b61026b6-73ac-4fc7-8ea2-1513a165fce5`。

这确认当前图片密钥所属分组没有能承接 `gpt-image-2` 的配置账号。请求未包含 response_format 或其他可选图片参数，仍得到相同错误，因此移除该参数不能单独恢复当前通道。需要检查代理分组的上游账号、可用模型/映射和图片权限，或配置该服务中具备图片能力的密钥。

历史两次 404 没有保存错误正文；当前是使用同一部署配置复现同类错误后的诊断，不能补造历史响应正文。

图片密钥与聊天密钥不同，两把密钥只读查询 `/models` 均返回 12 个模型且无图片模型。没有尝试自动改用聊天密钥；模型列表结果本身仍不是能力判定依据。

## 参数定义来源

官方文档站 `developers.openai.com` 和 `platform.openai.com` 的 image-generation/model/API reference 页面在此次环境中都返回 403，未取得正文，不能称为已读过在线官方文档。

改为核对 OpenAI 发布的 Python SDK：

- 项目安装版：`openai==1.109.1`。
- 只下载并读取 PyPI 最新发布 `openai==3.13.0` 的 wheel 中源文件，没有安装或升级项目依赖。
- 发布元数据：`https://pypi.org/pypi/openai/json`。wheel 的发布 SHA-256 为 `e35b1f6fe99245e86e37504d9fad1ad2a363807307c424232c1d849bd0666c8e`。
- 读取 `openai/types/image_model.py`、`image_generate_params.py`、`image_edit_params.py` 及 `resources/images.py`；这些参数文件声明由 OpenAPI spec 生成。
- 3.13.0 的 ImageModel 明确包含 `gpt-image-2`；没有把本任务模型换成其他型号。
- 生成参数 response_format 原文：`This parameter isn't supported for the GPT image models, which always return base64-encoded images.`
- 编辑参数 response_format 原文：`This parameter is only supported for dall-e-2 ... as GPT image models always return base64-encoded images.`
- input_fidelity 原文支持 `gpt-image-1`、`gpt-image-1.5 and later models`，不支持 mini；因此不能用旧 SDK 文档的旧型号列表断言 GPT Image 2 不支持 high。

源文件的本次本地读取副本保存在 `/tmp/aiqq-image-docs-oxl5rndz/latest-openai_*`，长期结论与必要原文保存在本文。

## 项目调用逐项结论

| 项目 | 核对结果 |
| --- | --- |
| 文生图 POST `/v1/images/generations`，JSON | 正确，实际 HTTP 捕获与当前官方 SDK 一致。 |
| 编辑 POST `/v1/images/edits`，multipart 文件上传 | 正确，文件 tuple 被 SDK 正确编码为 image 表单部分。 |
| model `gpt-image-2` | 官方 SDK 认可的模型名；当前代理密钥分组未配置可用账号。 |
| n=1、size=1024x1024、quality=medium | 符合 GPT 图片参数定义。 |
| output_format=jpeg、output_compression=85 | 符合格式和压缩范围定义。 |
| 编辑 input_fidelity=high | 符合当前参数定义，可保留。 |
| 文生图 moderation=auto | 符合当前参数定义，可保留。 |
| 两入口 response_format=b64_json | 不符合 GPT 图片模型参数定义；应移除，并继续读取响应中的 data[0].b64_json。 |
| max_retries=2 | SDK 在 429、5xx、超时等情况下会自动重发；与项目超时不重试的业务承诺有冲突，不是此次 404 的原因。 |
| 只记录 status/request_id，不记录错误摘要 | 丢失了当前这类“分组没有可用模型账号”的关键原因，应恢复脱敏诊断。 |
| 安装版 SDK 1.109.1 | 较旧，但允许 model 为字符串且已验证可编码所需参数；不能把它认定为 404 直接原因。 |

## 符合参数定义的调用骨架

前提：image_client 使用与图片服务匹配的 base_url、已开通目标模型的 API key。以下只表示调用契约，当前代理分组尚未恢复，未获得真实生成成功结果。

```python
parameters = dict(
    model="gpt-image-2",
    n=1,
    size="1024x1024",
    quality="medium",
    output_format="jpeg",
    output_compression=85,
)

# 文生图：SDK 自动发 JSON POST /images/generations。
generated = await image_client.images.generate(
    prompt=safe_prompt,
    moderation="auto",
    **parameters,
)

# 改图：SDK 自动编码 multipart，勿手工填写 boundary。
edited = await image_client.images.edit(
    image=("reference.png", reference_bytes, "image/png"),
    prompt=safe_edit_prompt,
    input_fidelity="high",
    **parameters,
)

# 两种响应均从 data[0].b64_json 取图片，再进行本项目既有解码/校验。
```

## 本次验证范围

使用已安装 SDK + httpx.MockTransport 捕获了现有实现的真实请求，确认 response_format 确实出现在 JSON 和 multipart 中。再次以去掉该字段的调用骨架进行离线验证，生成和编辑请求均正确编码，模拟响应 Base64 解码通过。

线上仅进行模型列表读取和缺少必填输入的路由诊断；没有创建图片、修改运行代码、变更密钥、升级依赖或重启服务。代理账号配置恢复后仍需真实生成和编辑验收。
