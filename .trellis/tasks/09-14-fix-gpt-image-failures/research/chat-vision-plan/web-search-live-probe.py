"""Bounded real web/image search through the deployed code under service restrictions.

Synthetic query, no production DB, QQ client, generated image or persistent pixels.
Print only safe diagnostic metadata and the deliberately public returned URLs.
"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import httpx
from dotenv import dotenv_values
from aiqq.config import AppConfig
from aiqq.services.ai.codex_sdk import CodexSDKBackend

SCHEMA = {
    'type': 'object', 'properties': {
        'web_title': {'type': 'string'}, 'web_url': {'type': 'string'},
        'image_url': {'type': 'string'}, 'success': {'type': 'boolean'},
        'failure_reason': {'type': 'string'},
    },
    'required': ['web_title', 'web_url', 'image_url', 'success', 'failure_reason'],
    'additionalProperties': False,
}
class EmptySession:
    async def read_history(self, **kwargs): return {'messages': [], 'has_more': False}
    async def read_image(self, **kwargs): return {'status': 'error', 'message': 'No chat image in this synthetic probe.'}, ()

async def main():
    config = AppConfig.from_env({**os.environ, **dotenv_values('/home/ubuntu/AiQQ/.env')})
    calls = []
    original_send = httpx.AsyncClient.send
    async def observed_send(client, request, **kwargs):
        entry = None
        if request.url.path.endswith('/alpha/search'):
            entry = {'path': '/alpha/search', 'method': request.method}
            calls.append(entry)
            if len(calls) > 4:
                raise RuntimeError('live search probe budget exceeded')
            try:
                payload = json.loads(request.content)
                entry['request_keys'] = sorted(payload) if isinstance(payload, dict) else []
                # Only operation names, never arbitrary request values.
                if isinstance(payload, dict):
                    for key in ('request', 'arguments', 'params'):
                        if isinstance(payload.get(key), dict): entry['operation_keys'] = sorted(payload[key])
            except (ValueError, httpx.RequestNotRead):
                pass
        try:
            response = await original_send(client, request, **kwargs)
            if entry is not None: entry['status'] = response.status_code
            return response
        except Exception as exc:
            if entry is not None: entry['error_type'] = type(exc).__name__
            raise
    started = time.monotonic()
    result = {'model': config.ai.model, 'success': False}
    with tempfile.TemporaryDirectory(prefix='aiqq-web-live-', dir='/var/lib/aiqq') as directory:
        root = Path(directory)
        backend = CodexSDKBackend(api_key=config.ai.api_key, base_url=config.ai.base_url,
            model=config.ai.model, cli_path=config.ai.codex_cli,
            runtime_dir=root/'runtime', work_dir=root/'work',
            turn_timeout_seconds=180, reasoning_effort=config.ai.reasoning_effort,
            utility_reasoning_effort=config.ai.utility_reasoning_effort,
            max_concurrent=1, process_environment=os.environ)
        try:
            with patch.object(httpx.AsyncClient, 'send', observed_send):
                async with asyncio.timeout(180):
                    answer = await backend.run(
                        instructions='You are testing web search. Actually use the web tool. Do not execute shell commands or generate images. Never claim success from memory. Return only the provided JSON schema.',
                        model_input='请做一次网页搜索查找 OpenAI Codex 官方文档，再打开一个搜索结果核实标题，最后做一次图片搜索寻找瀑布风景图片。最多调用3次 web.run。返回真实查到的文档标题、文档网址和一条真实图片直链。若某步骤失败，success=false并说明原因；全部成功则success=true。不要生成图片，不要读取群消息或本地文件。',
                        output_schema=SCHEMA, enable_web_search=config.ai.web_search_enabled,
                        enable_gpt_image_skill=config.ai.gpt_image_skill_enabled,
                        chat_read_session=EmptySession())
            parsed = json.loads(answer.text)
            result.update(answer=parsed, success=bool(parsed.get('success')) and bool(calls)
                          and all(call.get('status') == 200 for call in calls))
        except Exception as exc:
            result['error_type'] = type(exc).__name__
        finally:
            await backend.close()
            result['work_turns_remaining'] = len(list((root/'work').glob('turn-*')))
            result['runtime_turns_remaining'] = len(list((root/'runtime').glob('turn-*')))
    result.update(search_calls=calls, elapsed_seconds=round(time.monotonic()-started,2),
                  qq_send=False, image_generation=False, production_db_write=False)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result['success']: raise SystemExit(1)

if __name__ == '__main__': asyncio.run(main())
