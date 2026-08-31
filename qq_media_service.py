from typing import Any


class QQMediaError(RuntimeError):
    pass


async def upload_qq_image(message, public_url: str) -> dict[str, str]:
    api = getattr(message, "_api", None)
    if api is None:
        raise QQMediaError("QQ 消息缺少 API 客户端。")

    group_openid = getattr(message, "group_openid", None)
    try:
        if group_openid:
            result = await api.post_group_file(
                group_openid=group_openid,
                file_type=1,
                url=public_url,
                srv_send_msg=False,
            )
        else:
            author = getattr(message, "author", None)
            user_openid = getattr(author, "user_openid", None)
            if not user_openid:
                raise QQMediaError("无法确定 QQ 图片的接收方。")
            result = await api.post_c2c_file(
                openid=user_openid,
                file_type=1,
                url=public_url,
                srv_send_msg=False,
            )
    except QQMediaError:
        raise
    except Exception as exc:
        raise QQMediaError("调用 QQ 图片上传接口失败。") from exc

    if not isinstance(result, dict):
        raise QQMediaError("QQ 图片上传返回格式不正确。")
    file_info: Any = result.get("file_info")
    if not isinstance(file_info, str) or not file_info:
        raise QQMediaError("QQ 图片上传响应缺少 file_info。")
    return {"file_info": file_info}
