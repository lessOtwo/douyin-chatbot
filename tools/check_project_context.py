"""Focused check that an approved merchant's projects reach the model request."""
import json
from contextlib import closing
import os
from pathlib import Path
from queue import Queue
import socket
import sqlite3
import subprocess
import tempfile
from threading import Thread
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
BINARY=ROOT/"business-service/target/debug/douyin-chatbot-service.exe"
KEY="project-context-test-key-1234567890"
CAPTURE=Queue()

def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]

class Model(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CAPTURE.put(data)
        message=data['messages'][0]['content']
        refused='明确拒绝留联系方式' in message
        stopped='别再联系我' in message
        provided='13545678901' in message
        peer='你有全屋定制的装修需求吗' in message
        promised='等我把信息整理整理发给你' in message
        selection=message in ('你好','在吗')
        answer=("事业编培训不在本店服务范围内，这里主要提供语言培训和成人学历提升。" if '事业编' in message
                else "你好呀～想咨询语言培训、成人学历提升，还是其他相关需求呢？" if selection
                else "好的，您整理好发过来，我继续为您介绍。" if promised
                else "语言培训可以咨询课程安排，具体安排需门店确认。" if '语言培训' in message
                else "具体价格需要店里确认。" if '价格' in message
                else "这里可以咨询语言培训或成人学历提升。")
        body=json.dumps({"choices":[{"message":{"content":json.dumps(
            {"reply":"不用呢 谢谢" if peer else "好的，之后不再联系您。" if stopped else answer,
             "action":"peer" if peer else "stop" if stopped else "reply",
             "fields":{"phone":"13545678901","contact_method":"phone"} if provided
                      else {"contact_promised":True} if promised
                      else {"project_selection_only":True} if selection
                      else {"contact_refused":True} if refused else {}},ensure_ascii=False)}}]},
            ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def call(base,path,data=None):
    req=urllib.request.Request(base+path,
        data=None if data is None else json.dumps(data,ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type":"application/json","x-api-key":KEY})
    with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)

def main():
    assert BINARY.exists()
    model_port=port()
    model=ThreadingHTTPServer(("127.0.0.1",model_port),Model)
    Thread(target=model.serve_forever,daemon=True).start()
    base=f"http://127.0.0.1:{port()}"
    try:
        with tempfile.TemporaryDirectory(prefix="douyin-project-context-") as folder:
            temp=Path(folder)
            (temp/"accounts.json").write_text(json.dumps([{
                "id":"demo","merchant_id":"merchant-demo","industry":"教育教培",
                "fastgpt_app_id":"fixture-app","fastgpt_key_env":"FASTGPT_APP_KEY"
            }],ensure_ascii=False),encoding="utf-8")
            db_path=temp/"business.sqlite"
            profile={"fields":{"完整门店名称":"演示教培门店","所属行业":"教育教培",
                "服务区域":"青岛市","资料或服务":"预约课程咨询",
                "门店联系电话":"0532-12345678","门店电话可公开":"否"},
                "projects":{"main":[{"id":"main","name":"语言培训","question":"语言培训问句"}],
                    "available":[{"id":"available","name":"成人学历提升","question":"成人学历提升问句"}],
                    "excluded":[{"id":"excluded","name":"职业资格考证","question":"职业资格考证问句"}]}}
            with closing(sqlite3.connect(db_path)) as db:
                db.create_function("unixepoch",0,lambda:int(time.time()))
                db.executescript((ROOT/"business-service/src/schema.sql").read_text(encoding="utf-8"))
                db.execute("INSERT INTO merchants(id,current_version_id) VALUES('merchant-demo','version-demo')")
                db.execute("INSERT INTO merchant_versions(id,merchant_id,version,status,profile_json,contact_name,contact_phone) VALUES('version-demo','merchant-demo',1,'approved',?,'张先生','13800000000')",(json.dumps(profile,ensure_ascii=False),))
                db.commit()
            env={**os.environ,"BUSINESS_API_KEY":KEY,"INTERNAL_API_KEY":"project-context-internal-1234567890",
                "ACCOUNTS_FILE":str(temp/"accounts.json"),"DATABASE_PATH":str(db_path),
                "BIND":base.removeprefix("http://"),"SEND_MODE":"mock",
                "FASTGPT_URL":f"http://127.0.0.1:{model_port}","FASTGPT_APP_KEY":"fixture-key",
                "ADMIN_TOOLS_AUTO_LOGIN":"false"}
            with (temp/"service.log").open("w",encoding="utf-8") as log:
                service=subprocess.Popen([str(BINARY)],env=env,stdout=log,stderr=log)
                try:
                    for _ in range(100):
                        try:
                            if call(base,"/health")["status"]=="ok":break
                        except (OSError,urllib.error.URLError):time.sleep(.1)
                    else:raise AssertionError("service failed to start")
                    call(base,"/api/simulate/demo",{"event_id":"e1","message_id":"m1",
                        "conversation_id":"c1","customer_id":"u1","customer_nickname":"全屋定制联系我","text":"你好"})
                    request=CAPTURE.get(timeout=10)
                    variables=request["variables"]
                    assert request["messages"]==[{"role":"user","content":"你好"}]
                    assert request["appId"]=="fixture-app" and request["chatId"]
                    assert variables["project_selection_question"]=="你好呀～想咨询语言培训、成人学历提升，还是其他相关需求呢？"
                    assert "职业资格考证" not in variables["project_selection_question"]
                    sent_profile=json.loads(variables["merchant_profile"])
                    assert len(sent_profile["projects"]["main"])==1
                    assert "门店联系电话" not in sent_profile["fields"]
                    assert variables["industry"]=="教育教培"
                    assert "customer_nickname" not in variables
                    def reply(event_id):
                        for _ in range(100):
                            with closing(sqlite3.connect(db_path)) as check:
                                rows=check.execute("SELECT o.text,o.status FROM outbox o JOIN events e ON e.id=o.event_id WHERE e.event_id=? ORDER BY o.part_index",(event_id,)).fetchall()
                            if rows and all(row[1]=='mock_sent' for row in rows):return [row[0] for row in rows]
                            time.sleep(.1)
                        raise AssertionError(f"missing outbox for {event_id}")
                    assert "留个联系方式" not in "".join(reply("e1"))
                    replies=[]
                    for number,text in [(2,"语言培训"),(3,"先介绍一下"),(4,"我明确拒绝留联系方式"),
                                        (5,"别再联系我"),(6,"还能介绍吗"),(7,"我的号码是13545678901"),
                                        (8,"现在价格多少")]:
                        call(base,"/api/simulate/demo",{"event_id":f"e{number}","message_id":f"m{number}",
                            "conversation_id":"c1","customer_id":"u1","text":text})
                        CAPTURE.get(timeout=10)
                        parts=reply(f"e{number}")
                        joined="".join(parts)
                        if number < 7:
                            assert joined.count("留个联系方式")==1 and "留个联系方式" in parts[-1],parts
                            assert "不再联系" not in joined,parts
                            replies.append(joined[joined.index("留个联系方式")-3:])
                        elif number == 7:
                            assert joined=="收到，稍后店长联系您详聊。",parts
                        else:
                            assert "具体价格需要店里确认" in joined and "留个联系方式" not in joined,parts
                        assert "电话" not in joined and "微信" not in joined,parts
                    assert len(set(replies))>=4,replies
                    for number,text in [(12,"在吗"),(13,"语言培训"),(14,"我考虑下"),
                                        (15,"行，你等我把信息整理整理发给你"),(16,"价格多少")]:
                        call(base,"/api/simulate/demo",{"event_id":f"e{number}","message_id":f"m{number}",
                            "conversation_id":"c4","customer_id":"u4","text":text})
                        CAPTURE.get(timeout=10)
                        joined="".join(reply(f"e{number}"))
                        assert ("留个联系方式" in joined)==(number in (13,14)),joined
                    with closing(sqlite3.connect(db_path)) as check:
                        assert check.execute("SELECT contact_promised FROM conversations WHERE external_id='c4'").fetchone()==(1,)
                    call(base,"/api/simulate/demo",{"event_id":"e11","message_id":"m11",
                        "conversation_id":"c3","customer_id":"u3","text":"事业编培训"})
                    CAPTURE.get(timeout=10)
                    out_of_scope="".join(reply("e11"))
                    assert "事业编培训不在本店服务范围" in out_of_scope and "留个联系方式" in out_of_scope,out_of_scope
                    call(base,"/api/simulate/demo",{"event_id":"e9","message_id":"m9",
                        "conversation_id":"c2","customer_id":"u2","customer_nickname":"全屋定制联系我",
                        "text":"你有全屋定制的装修需求吗？有的话联系我"})
                    CAPTURE.get(timeout=10)
                    assert reply("e9")==["不用呢 谢谢"]
                    call(base,"/api/simulate/demo",{"event_id":"e10","message_id":"m10",
                        "conversation_id":"c2","customer_id":"u2","text":"我想咨询语言培训"})
                    assert reply("e10")==["不用呢 谢谢"]
                    print(json.dumps({"result":"passed","checks":["one-turn message",
                        "approved project snapshot injected","non-public phone excluded",
                        "nickname omitted from model request", "answer then ask once with varied wording",
                        "out-of-scope project answered from approved merchant profile", "refusal and stop do not suppress invitation",
                        "provided contact acknowledged then answers without asking",
                        "nickname alone is not peer but message promotion is persistent peer",
                        "project selection omits contact invitation", "promised later contact suppresses later invitations"]},
                        ensure_ascii=False))
                finally:
                    service.terminate();service.wait(timeout=10)
    finally:model.shutdown()

if __name__=="__main__":main()
