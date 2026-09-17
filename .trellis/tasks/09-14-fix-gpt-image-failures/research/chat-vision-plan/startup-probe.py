"""Offline launcher probe; dummy keys, no upstream/model/QQ calls."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from aiqq.services.ai.chat_runtime import ChatRuntime
from aiqq.services.ai.codex_sdk import find_codex_cli

async def main():
    with tempfile.TemporaryDirectory(prefix='startup-probe-',dir='/var/lib/aiqq') as folder:
        root=Path(folder); runtime=root/'runtime'; work=root/'work'
        runtime.mkdir(); work.mkdir()
        async with ChatRuntime(session=None,work=work,runtime=runtime,
                               cli=find_codex_cli(),api_key='dummy-unused',
                               base_url='https://unused.invalid/v1',model='model') as bridge:
            process=await asyncio.create_subprocess_exec(str(bridge.launcher),'--version',
                env={'PATH':'/usr/bin:/bin','HOME':str(runtime),'CODEX_HOME':str(runtime)},
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await process.communicate()
            print(json.dumps({'exit':process.returncode,'stdout':stdout.decode(),'stderr':stderr.decode()}))
asyncio.run(main())
