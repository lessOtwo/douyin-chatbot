"""Explicit FastGPT HTTP fixture for local business-flow checks only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,os,time
calls=[]
histories={}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  data=json.dumps({'calls':calls,'histories':histories}).encode();self.send_response(200);self.end_headers();self.wfile.write(data)
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  if len(body.get('messages',[]))!=1 or body['messages'][0].get('role')!='user' or not body.get('chatId'):
   self.send_response(422);self.end_headers();return
  text=body['messages'][0]['content'];chat_id=body['chatId']
  calls.append({'text':text,'chatId':chat_id,'messages':body['messages'],'variables':body.get('variables')})
  history=histories.setdefault(chat_id,[]);history.append({'role':'user','content':text})
  if 'slow' in text:time.sleep(4)
  action='unanswered' if 'unanswered' in text else 'handoff' if 'human' in text or 'legacy-handoff' in text else 'stop' if 'stop' in text else 'reply'
  decision={'reply':'fixture reply: '+text,'action':action,'fields':{}}
  if text == 'split-long-reply':
   decision['reply']='你好呀，这边可以先帮你看看具体需求。关于课程内容和时间安排，我再根据你关注的方向说明清楚呢。如果还有别的问题也可以接着问我哦。'
  if action=='unanswered':decision['reason']='缺少当前商家的材料品牌资料'
  if '13800000000' in text:decision['fields']['phone']='13800000000'
  history.append({'role':'assistant','content':decision['reply']})
  if 'fail-after-save' in text:
   self.send_response(502);self.end_headers();return
  data=json.dumps({'choices':[{'message':{'content':json.dumps(decision)}}]}).encode()
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)
ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('MOCK_PORT','9000'))),Handler).serve_forever()
