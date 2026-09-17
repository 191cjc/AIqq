"""One bounded vision-only probe; no QQ client, generation or production DB writes."""
import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import time

from dotenv import dotenv_values
from PIL import Image, ImageDraw, ImageFont

from aiqq.config import AppConfig
from aiqq.database import GroupMessageRepository, SQLiteConnection
from aiqq.logic.models import ConversationRequest
from aiqq.services.agents.chat import ChatAgent
from aiqq.services.ai.codex_sdk import CodexSDKBackend
from aiqq.services.chat_read import GroupChatReadService
from aiqq.services.images.chat_decode import decode_chat_image


async def main():
    config = AppConfig.from_env({**os.environ, **dotenv_values('/home/ubuntu/AiQQ/.env')})
    expected = ('COPPER 7429', 'ORBIT 3816')
    data_by_url = {}
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 72)
    for index, word in enumerate(expected, 1):
        card = Image.new('RGB', (900, 260), 'white')
        ImageDraw.Draw(card).text((40, 80), word, font=font, fill='black')
        output = BytesIO()
        card.save(output, format='PNG')
        data_by_url[f'https://vision-probe.invalid/card-{index}.png'] = output.getvalue()

    class PreparedCards:
        def __init__(self):
            self.reads = []

        async def download_for_read(self, url):
            self.reads.append(url)
            return decode_chat_image(data_by_url[url])

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='aiqq-live-vision-') as directory:
        root = Path(directory)
        repository = GroupMessageRepository(SQLiteConnection(root / 'messages.db'))
        await repository.initialize()
        backend = CodexSDKBackend(
            api_key=config.ai.api_key, base_url=config.ai.base_url, model=config.ai.model,
            cli_path=config.ai.codex_cli, runtime_dir=root / 'runtime', work_dir=root / 'work',
            turn_timeout_seconds=config.ai.request_timeout_seconds,
            reasoning_effort=config.ai.reasoning_effort,
            utility_reasoning_effort=config.ai.utility_reasoning_effort,
            max_concurrent=1, process_environment=os.environ,
        )
        cards = PreparedCards()
        try:
            for index in (1, 2):
                event = {'op': 0, 't': 'GROUP_MESSAGE_CREATE', 's': index, 'id': f'probe-{index}',
                         'd': {'id': f'card-{index}', 'group_openid': 'vision-probe',
                               'author': {'member_openid': 'probe-user'},
                               'content': '', 'timestamp': '2026-09-16T08:00:00Z',
                               'attachments': [{'content_type': 'image/png',
                                                'url': f'https://vision-probe.invalid/card-{index}.png'}]}}
                await repository.add_gateway_event(event['t'], event, raw_text=json.dumps(event))
            current = await repository.get_current_for_reference('vision-probe', 'card-2')
            history = await repository.list_for_reference('vision-probe', before_message_id='card-2', limit=50)
            agent = ChatAgent(backend, system_prompt='准确读取图片，使用简洁中文回答。', web_search_enabled=False,
                              chat_read_service=GroupChatReadService(repository=repository, downloader=cards))
            request = ConversationRequest(
                user_input='请用读取脚本分别读取记录1和记录2的图片，然后用view_image查看两张图。完整回答只写每个记录号与图片上实际看到的文字。',
                group_history=history, reference_material_available=True, conversation_key='vision-probe:user',
                group_id='vision-probe', current_message=current, history_has_more=False,
            )
            async with asyncio.timeout(240):
                answer = await agent.run(request)
            result = {'model': config.ai.model, 'success': all(word in answer.full_text for word in expected),
                      'image_sources_read': len(set(cards.reads)), 'answer': answer.full_text,
                      'elapsed_seconds': round(time.monotonic() - started, 2),
                      'generation_requested': answer.image_action is not None,
                      'work_turns_remaining': len(list((root / 'work').glob('turn-*'))),
                      'runtime_turns_remaining': len(list((root / 'runtime').glob('turn-*')))}
        except Exception as exc:
            result = {'model': config.ai.model, 'success': False, 'error_type': type(exc).__name__,
                      'image_sources_read': len(set(cards.reads)),
                      'elapsed_seconds': round(time.monotonic() - started, 2)}
        finally:
            await backend.close()
            await repository.close()
    Path(__file__).with_name('live-vision-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
