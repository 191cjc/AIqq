"""Offline diagnostic: genuine Codex web tool against synthetic local SSE."""
import asyncio
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from aiohttp import web
from aiqq.services.ai.codex_sdk import CodexSDKBackend

SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}
class Session:
    async def read_history(self, **kwargs): return {'messages': [], 'has_more': False}
    async def read_image(self, **kwargs): return {'status': 'error', 'message': 'unused'}, ()

async def main():
    model_calls = []
    bridge_calls = []
    tool_results = []
    @web.middleware
    async def trace(request, handler):
        try:
            response = await handler(request)
            bridge_calls.append({'method': request.method, 'path': request.path, 'status': response.status})
            return response
        except web.HTTPException as exc:
            bridge_calls.append({'method': request.method, 'path': request.path, 'status': exc.status})
            raise
    async def endpoint(request):
        payload = await request.json()
        model_calls.append(request.path)
        if len(model_calls) == 1:
            print(json.dumps({'tool_shapes': [{'type': t.get('type'), 'name': t.get('name'), 'tools': [{'name': n.get('name'), 'type': n.get('type')} for n in t.get('tools', [])]} for t in payload.get('tools', [])]}), flush=True)
            item = {'id':'call1','type':'function_call','call_id':'call1','name':'run','namespace':'web','arguments':json.dumps({'search_query':[{'q':'OpenAI Codex documentation'}],'response_length':'short'}),'status':'completed'}
        else:
            for i in payload.get('input', []):
                if i.get('type') in ('function_call_output', 'custom_tool_call_output'):
                    tool_results.append({k:i.get(k) for k in ('type', 'call_id', 'output')})
            item = {'id':'m','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'{"ok":true}','annotations':[]}]}
        response = {'id':'r'+str(len(model_calls)),'object':'response','model':payload['model'],'status':'completed','output':[item],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
        events = [{'type':'response.created','response':{**response,'status':'in_progress','output':[]}},{'type':'response.output_item.added','output_index':0,'item':item}]
        if len(model_calls)>1:events.append({'type':'response.output_text.delta','output_index':0,'content_index':0,'delta':'{"ok":true}'})
        events.extend([{'type':'response.output_item.done','output_index':0,'item':item},{'type':'response.completed','response':response}])
        return web.Response(text=''.join('data: '+json.dumps(e)+'\n\n' for e in events),content_type='text/event-stream')
    app=web.Application();app.router.add_post('/responses',endpoint)
    runner=web.AppRunner(app,access_log=None);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start();port=site._server.sockets[0].getsockname()[1]
    app_class=web.Application
    def traced_app(*args,**kwargs): return app_class(*args,middlewares=[trace],**kwargs)
    try:
        with tempfile.TemporaryDirectory(prefix='aiqq-search-probe-',dir='/var/lib/aiqq') as folder:
            backend=CodexSDKBackend(api_key='synthetic-key',base_url=f'http://127.0.0.1:{port}',model='gpt-6-astra',runtime_dir=Path(folder)/'runtime',work_dir=Path(folder)/'work')
            try:
                with patch('aiqq.services.ai.chat_runtime.web.Application',traced_app):
                    async with asyncio.timeout(30):
                        answer=await backend.run(instructions='Search with web and return JSON.',model_input='Search for OpenAI Codex documentation.',output_schema=SCHEMA,enable_web_search=True,chat_read_session=Session())
                print(json.dumps({'answer':answer.text,'model_calls':model_calls,'bridge_calls':bridge_calls,'tool_results':tool_results},ensure_ascii=False),flush=True)
            finally: await backend.close()
    finally: await runner.cleanup()

asyncio.run(main())
