"""Offline synthetic Responses probe with production feature flags and paths."""
import asyncio,json,tempfile
from pathlib import Path
from aiohttp import web
from aiqq.services.ai.codex_sdk import CodexSDKBackend

SCHEMA={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
class Session:
 async def read_history(self,**kwargs):return {'messages':[],'has_more':False}
 async def read_image(self,**kwargs):return {'status':'error','message':'unused'},()
async def main():
 calls=[]
 async def endpoint(request):
  payload=await request.json(); calls.append(1)
  item={'id':'m','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'{"ok":true}','annotations':[]}]}
  response={'id':'r','object':'response','model':payload['model'],'status':'completed','output':[item],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
  events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]}}, {'type':'response.output_item.added','output_index':0,'item':item},{'type':'response.output_text.delta','output_index':0,'content_index':0,'delta':'{"ok":true}'},{'type':'response.output_item.done','output_index':0,'item':item},{'type':'response.completed','response':response}]
  return web.Response(text=''.join('data: '+json.dumps(e)+'\n\n' for e in events),content_type='text/event-stream')
 app=web.Application();app.router.add_post('/responses',endpoint);runner=web.AppRunner(app,access_log=None);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();port=site._server.sockets[0].getsockname()[1]
 try:
  for flags in (False,True):
   with tempfile.TemporaryDirectory(prefix='chat-start-probe-',dir='/var/lib/aiqq') as folder:
    backend=CodexSDKBackend(api_key='dummy-unused-key',base_url=f'http://127.0.0.1:{port}',model='gpt-6-astra',runtime_dir=Path(folder)/'runtime',work_dir=Path(folder)/'work')
    try:
     async with asyncio.timeout(20): result=await backend.run(instructions='Return ok JSON.',model_input='hello',output_schema=SCHEMA,enable_web_search=flags,enable_gpt_image_skill=flags,chat_read_session=Session())
     print(json.dumps({'features':flags,'success':True,'text':result.text,'requests':len(calls)}))
    except Exception as exc:
     causes=[]
     while exc:
      causes.append({'type':type(exc).__name__,'message':str(exc)[:5000]});exc=exc.__cause__
     print(json.dumps({'features':flags,'success':False,'causes':causes,'requests':len(calls)}))
    finally:await backend.close()
 finally:await runner.cleanup()
asyncio.run(main())
