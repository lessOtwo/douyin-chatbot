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
        body=json.dumps({"choices":[{"message":{"content":json.dumps(
            {"reply":"您好，请说说需求。","action":"reply","fields":{}},ensure_ascii=False)}}]},
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
                        "conversation_id":"c1","customer_id":"u1","text":"你好"})
                    request=CAPTURE.get(timeout=10)
                    variables=request["variables"]
                    assert request["messages"]==[{"role":"user","content":"你好"}]
                    assert request["appId"]=="fixture-app" and request["chatId"]
                    assert variables["project_selection_question"]=="您好，您想咨询语言培训、成人学历提升还是其他相关需求？"
                    assert "职业资格考证" not in variables["project_selection_question"]
                    sent_profile=json.loads(variables["merchant_profile"])
                    assert len(sent_profile["projects"]["main"])==1
                    assert "门店联系电话" not in sent_profile["fields"]
                    assert variables["industry"]=="教育教培"
                    print(json.dumps({"result":"passed","checks":["one-turn message",
                        "approved project snapshot injected","non-public phone excluded",
                        "generic question lists main then available projects and excludes unavailable ones"]},
                        ensure_ascii=False))
                finally:
                    service.terminate();service.wait(timeout=10)
    finally:model.shutdown()

if __name__=="__main__":main()
