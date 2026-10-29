"""Focused isolated merchant invitation, form constraints, review and version checks."""
import json
from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
BINARY=ROOT/"business-service/target/debug/douyin-chatbot-service.exe"
KEY="onboarding-focused-test-key-1234567890"
WORKFLOW_ID="6ad7276957c1257e1bb5ed75"
DRAFT_ID="6ad7276957c1257e1bb5ed76"

class FastGPTMock(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,data,cookie=False):
        payload=json.dumps({"code":200,"data":data}).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(payload)))
        if cookie:self.send_header("Set-Cookie","fastgpt_token="+"x"*40+"; Path=/")
        self.end_headers();self.wfile.write(payload)
    def do_GET(self):
        if self.path.startswith("/api/support/user/account/preLogin"):
            self.respond({"code":"test"})
        elif self.path.startswith("/api/core/app/detail"):
            app_id=self.path.split("appId=",1)[-1]
            self.respond({"_id":app_id,"type":"advanced",
                "publishedVersionId":"published" if app_id==WORKFLOW_ID else None})
        else:self.send_error(404)
    def do_POST(self):
        size=int(self.headers.get("Content-Length","0"))
        body=json.loads(self.rfile.read(size) or b"{}")
        if self.path.endswith("loginByPassword"):
            self.respond({"status":"ok"},cookie=True)
        elif self.path.endswith("listV2"):
            parent=body.get("parentId")
            items=([{"_id":"6ad7276957c1257e1bb5ed77","name":"门店文件夹","type":"folder"},
                    {"_id":DRAFT_ID,"name":"未发布工作流","type":"advanced"}]
                   if not parent else [{"_id":WORKFLOW_ID,"name":"已发布留资工作流","type":"advanced"}])
            self.respond({"list":items,"total":len(items)})
        else:self.send_error(404)

def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]

def call(base,path,data=None,auth=False,method=None):
    request=urllib.request.Request(base+path,
        data=None if data is None else json.dumps(data,ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type":"application/json",**({"x-api-key":KEY} if auth else {})},method=method)
    with urllib.request.urlopen(request,timeout=12) as response:
        return json.load(response)

def expect_http(code,fn):
    try:fn()
    except urllib.error.HTTPError as error:assert error.code==code,(error.code,code,error.read())
    else:raise AssertionError(f"expected HTTP {code}")

def form(token,industry,project,store="演示装修门店"):
    return {"token":token,"industry_id":industry,"main_project_ids":[project],
        "available_project_ids":[],"excluded_project_ids":[],
        "fields":{"douyin_account":"1615185357","douyin_nickname":"东营大美聊装修","store_name":store,
            "service_area":"青岛市",
            "business_hours":"9:00-18:00","address":"青岛市市南区示例路1号",
            "store_phone":"0532-12345678","store_phone_public":False,
            "materials_or_services":"门店提供装修资料并预约电话沟通"},
        "contact_name":"张先生","contact_phone":"13800000000"}

def main():
    assert BINARY.exists()
    html=(ROOT/"business-service/web/onboard.html").read_text(encoding="utf-8")
    assert 'id="douyinNickname"' in html and 'pattern="[0-9]+"' in html
    assert 'id="warranty"' not in html and 'id="duration"' not in html
    assert '请包含完整门店名称' not in html
    script=html.rsplit("<script>",1)[1].split("</script>",1)[0]
    subprocess.run(["node","--check","--input-type=commonjs"],input=script,text=True,check=True)
    base=f"http://127.0.0.1:{port()}"
    fastgpt=ThreadingHTTPServer(("127.0.0.1",0),FastGPTMock)
    fastgpt_thread=threading.Thread(target=fastgpt.serve_forever,daemon=True)
    fastgpt_thread.start()
    with tempfile.TemporaryDirectory(prefix="douyin-onboarding-") as folder:
        temp=Path(folder)
        accounts=temp/"accounts.json";accounts.write_text(json.dumps([{
            "id":"demo","merchant_id":"merchant-demo","industry":"装修装饰",
            "fastgpt_app_id":"","fastgpt_key_env":"FASTGPT_APP_KEY"
        }],ensure_ascii=False),encoding="utf-8")
        db_path=temp/"business.sqlite"
        env={**os.environ,"BUSINESS_API_KEY":KEY,
            "INTERNAL_API_KEY":"onboarding-internal-test-key-1234567890",
            "ACCOUNTS_FILE":str(accounts),"DATABASE_PATH":str(db_path),
            "BIND":base.removeprefix("http://"),"SEND_MODE":"mock",
            "ADMIN_TOOLS_AUTO_LOGIN":"false",
            "FASTGPT_URL":f"http://127.0.0.1:{fastgpt.server_port}",
            "FASTGPT_ROOT_PASSWORD":"test-password","FASTGPT_APP_KEY":"fastgpt-test-key"}
        with (temp/"service.log").open("w",encoding="utf-8") as log:
            service=subprocess.Popen([str(BINARY)],env=env,stdout=log,stderr=log)
            try:
                for _ in range(100):
                    try:
                        if call(base,"/health")["status"]=="ok":break
                    except (OSError,urllib.error.URLError):time.sleep(.1)
                else:raise AssertionError("service failed to start")
                industry=call(base,"/api/catalog/industries",{"name":"装修装饰"},True)["id"]
                other=call(base,"/api/catalog/industries",{"name":"教育教培"},True)["id"]
                for item in (industry,other):call(base,f"/api/catalog/industries/{item}/publish",{},True)
                project=call(base,"/api/catalog/projects",{"industry_id":industry,
                    "name":"老房翻新","question":"您想做局部改造还是整屋翻新？"},True)["id"]
                other_project=call(base,"/api/catalog/projects",{"industry_id":other,
                    "name":"语言培训","question":"您想学哪种语言？"},True)["id"]
                for item in (project,other_project):call(base,f"/api/catalog/projects/{item}/publish",{},True)
                invite=call(base,"/api/invitations",{},True)
                assert invite["expires_hours"]==6
                listed={item["id"]:item for item in call(base,"/api/invitations",auth=True)["items"]}
                assert listed[invite["id"]]["expires_at"]-listed[invite["id"]]["created_at"]==6*3600
                for hours in (12,24):
                    timed=call(base,"/api/invitations",{"expires_hours":hours},True)
                    listed={item["id"]:item for item in call(base,"/api/invitations",auth=True)["items"]}
                    assert listed[timed["id"]]["expires_at"]-listed[timed["id"]]["created_at"]==hours*3600
                expect_http(422,lambda:call(base,"/api/invitations",{"expires_hours":7},True))
                token=invite["token"]
                expect_http(404,lambda:urllib.request.urlopen(base+"/onboard"))
                with urllib.request.urlopen(base+"/onboard?token="+token) as response:
                    assert response.status==200 and b"<html" in response.read(200)
                context=call(base,"/api/onboard/context?token="+token)
                assert len(context["industries"])==2 and len(context["projects"])==2
                valid=form(token,industry,project)
                bad={**valid,"fields":{**valid["fields"],"douyin_account":"东营大美聊装修"}}
                expect_http(422,lambda:call(base,"/api/onboard/preview",bad))
                bad={**valid,"fields":{**valid["fields"],"douyin_nickname":""}}
                expect_http(422,lambda:call(base,"/api/onboard/preview",bad))
                bad={**valid,"main_project_ids":[other_project]}
                expect_http(422,lambda:call(base,"/api/onboard/preview",bad))
                bad={**valid,"available_project_ids":[project]}
                expect_http(422,lambda:call(base,"/api/onboard/preview",bad))
                preview=call(base,"/api/onboard/preview",valid)
                assert preview["valid"] and preview["profile"]["projects"]["main"][0]["question"]
                assert preview["profile"]["fields"]["门店详细地址"]=="青岛市市南区示例路1号"
                assert "质保售后" not in preview["profile"]["fields"]
                assert "产品施工时长" not in preview["profile"]["fields"]
                first=call(base,"/api/onboard/submit",valid)
                assert first["version"]==1 and first["status"]=="pending"
                assert next(item for item in call(base,"/api/invitations",auth=True)["items"]
                            if item["id"]==invite["id"])["merchant_id"]==first["merchant_id"]
                expect_http(404,lambda:call(base,"/api/onboard/submit",valid))
                expect_http(404,lambda:call(base,"/api/onboard/context?token="+token))
                workflows=call(base,"/api/fastgpt/workflows",auth=True)
                assert workflows["call_ready"] and workflows["items"]==[{"id":WORKFLOW_ID,"name":"已发布留资工作流"}]
                expect_http(422,lambda:call(base,"/api/merchant-versions/"+first["version_id"]+"/review",{"decision":"approved"},True))
                expect_http(422,lambda:call(base,"/api/merchant-versions/"+first["version_id"]+"/review",{"decision":"approved","fastgpt_app_id":DRAFT_ID},True))
                assert next(item for item in call(base,"/api/merchants",auth=True)["items"] if item["id"]==first["version_id"])["status"]=="pending"
                call(base,"/api/merchant-versions/"+first["version_id"]+"/review",{"decision":"approved","fastgpt_app_id":WORKFLOW_ID},True)
                registered=call(base,"/api/accounts",auth=True)["accounts"]
                assert any(item["id"]=="1615185357" and item["merchant_id"]==first["merchant_id"] and item["douyin_nickname"]=="东营大美聊装修" and item["fastgpt_app_id"]==WORKFLOW_ID for item in registered)
                received=call(base,"/webhooks/platform/1615185357",{
                    "event_id":"numeric-account-event","message_id":"numeric-account-message",
                    "conversation_id":"numeric-account-conversation","customer_id":"customer-1","text":"你好"},True)
                assert received["id"]
                expect_http(409,lambda:call(base,f"/api/catalog/projects/{project}",{},True,"DELETE"))
                expect_http(409,lambda:call(base,f"/api/catalog/industries/{industry}",{},True,"DELETE"))
                reinvite=call(base,"/api/invitations",{"merchant_id":first["merchant_id"]},True)
                second_form=form(reinvite["token"],industry,project,"演示装修门店二店")
                second_form["fields"]["douyin_nickname"]="东营大美聊装修二店"
                second=call(base,"/api/onboard/submit",second_form)
                assert second["version"]==2
                versions=call(base,"/api/merchants",auth=True)["items"]
                assert versions[0]["status"]=="pending" and versions[1]["current"]
                call(base,"/api/merchant-versions/"+second["version_id"]+"/review",{"decision":"approved","fastgpt_app_id":WORKFLOW_ID},True)
                updated_accounts=call(base,"/api/accounts",auth=True)["accounts"]
                assert sum(item["id"]=="1615185357" for item in updated_accounts)==1
                assert next(item for item in updated_accounts if item["id"]=="1615185357")["douyin_nickname"]=="东营大美聊装修二店"
                changed_invite=call(base,"/api/invitations",{"merchant_id":first["merchant_id"]},True)
                changed_form=form(changed_invite["token"],industry,project)
                changed_form["fields"]["douyin_account"]="1615185358"
                changed=call(base,"/api/onboard/submit",changed_form)
                expect_http(409,lambda:call(base,"/api/merchant-versions/"+changed["version_id"]+"/review",{"decision":"approved","fastgpt_app_id":WORKFLOW_ID},True))
                duplicate_invite=call(base,"/api/invitations",{},True)
                duplicate=call(base,"/api/onboard/submit",form(duplicate_invite["token"],industry,project,"另一家门店"))
                expect_http(409,lambda:call(base,"/api/merchant-versions/"+duplicate["version_id"]+"/review",{"decision":"approved","fastgpt_app_id":WORKFLOW_ID},True))
                rejected_invite=call(base,"/api/invitations",{},True)
                rejected_form=form(rejected_invite["token"],industry,project,"待修改门店")
                rejected_form["fields"]["douyin_account"]="1615185360"
                rejected_first=call(base,"/api/onboard/submit",rejected_form)
                reject_path="/api/merchant-versions/"+rejected_first["version_id"]+"/review"
                expect_http(422,lambda:call(base,reject_path,{"decision":"rejected"},True))
                rejected=call(base,reject_path,{"decision":"rejected","reason":"请补全服务区域"},True)
                assert rejected["token"] and rejected["expires_hours"]==6
                assert next(item for item in call(base,"/api/merchants",auth=True)["items"] if item["id"]==rejected_first["version_id"])["review_reason"]=="请补全服务区域"
                expect_http(404,lambda:call(base,"/api/onboard/context?token="+rejected_invite["token"]))
                retry_context=call(base,"/api/onboard/context?token="+rejected["token"])
                assert retry_context["prefill"]["review_reason"]=="请补全服务区域"
                assert retry_context["prefill"]["profile"]["fields"]["完整门店名称"]=="待修改门店"
                assert retry_context["prefill"]["profile"]["projects"]["main"][0]["id"]==project
                reissued=call(base,"/api/invitations",{"merchant_id":rejected_first["merchant_id"]},True)
                assert call(base,"/api/onboard/context?token="+reissued["token"])["prefill"]["review_reason"]=="请补全服务区域"
                retry_form=form(rejected["token"],industry,project,"已修改门店")
                retry_form["fields"]["douyin_account"]="1615185360"
                assert call(base,"/api/onboard/preview",retry_form)["valid"]
                rejected_second=call(base,"/api/onboard/submit",retry_form)
                assert rejected_second["version"]==2 and rejected_second["status"]=="pending"
                assert not any(a["id"]=="1615185360" for a in call(base,"/api/accounts",auth=True)["accounts"])
                expired=call(base,"/api/invitations",{},True)
                with closing(sqlite3.connect(db_path)) as db:
                    db.execute("UPDATE merchant_invitations SET expires_at=? WHERE id=?",(int(time.time())-1,expired["id"]))
                    db.commit()
                expect_http(404,lambda:call(base,"/api/onboard/context?token="+expired["token"]))
                revoked=call(base,"/api/invitations",{},True)
                call(base,"/api/invitations/"+revoked["id"]+"/revoke",{},True)
                call(base,"/api/invitations/"+revoked["id"],{},True,"DELETE")
                assert revoked["id"] not in [item["id"] for item in call(base,"/api/invitations",auth=True)["items"]]
                assert sorted(item["id"] for item in call(base,"/api/accounts",auth=True)["accounts"])==["1615185357","demo"]
                call(base,"/api/accounts/demo",{},True,"DELETE")
                assert [item["id"] for item in call(base,"/api/accounts",auth=True)["accounts"]]==["1615185357"]
                expect_http(404,lambda:call(base,"/webhooks/platform/demo",{
                    "event_id":"e1","message_id":"m1","conversation_id":"c1",
                    "customer_id":"u1","text":"你好"},True))
                service.terminate();service.wait(timeout=10)
                service=subprocess.Popen([str(BINARY)],env=env,stdout=log,stderr=log)
                for _ in range(100):
                    try:
                        if call(base,"/health")["status"]=="ok":break
                    except (OSError,urllib.error.URLError):time.sleep(.1)
                else:raise AssertionError("service failed to restart")
                assert [item["id"] for item in call(base,"/api/accounts",auth=True)["accounts"]]==["1615185357"]
                print(json.dumps({"result":"passed","checks":["token page gate and expiry",
                    "cross-industry and duplicate project rejection","one-use invitation",
                    "reviewed profile version preserved","used catalog deletion blocked",
                    "mandatory published workflow binding and restart persistence","numeric account and nickname validation",
                    "duplicate and changed account rejection","return reason, new invitation and prefill",
                    "optional address without store name",
                    "removed after-sales and duration fields","revoked invitation removal",
                    "account access removal","merchant JavaScript syntax"]},ensure_ascii=False))
            finally:
                service.terminate();service.wait(timeout=10)
    fastgpt.shutdown();fastgpt.server_close()

if __name__=="__main__":main()
