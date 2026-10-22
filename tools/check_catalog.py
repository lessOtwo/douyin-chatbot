"""Focused isolated check of industry/project draft and publish behavior."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "business-service/target/debug/douyin-chatbot-service.exe"

def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

def call(base, path, data=None, key=None, method=None):
    headers = {"Content-Type": "application/json"}
    if key: headers["x-api-key"] = key
    req = urllib.request.Request(base + path,
        data=None if data is None else json.dumps(data,ensure_ascii=False).encode("utf-8"),
        headers=headers,method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)

def main():
    assert BINARY.exists(), "build business-service first"
    key = "catalog-focused-test-key-1234567890"
    base = f"http://127.0.0.1:{port()}"
    with tempfile.TemporaryDirectory(prefix="douyin-catalog-") as folder:
        temp = Path(folder)
        accounts = temp / "accounts.json"
        accounts.write_text("[]\n",encoding="utf-8")
        env = {**os.environ,"BUSINESS_API_KEY":key,
               "INTERNAL_API_KEY":"catalog-internal-test-key-1234567890",
               "ACCOUNTS_FILE":str(accounts),"DATABASE_PATH":str(temp/"business.sqlite"),
               "BIND":base.removeprefix("http://"),"SEND_MODE":"mock",
               "ADMIN_TOOLS_AUTO_LOGIN":"false"}
        with (temp/"service.log").open("w",encoding="utf-8") as log:
            html=(ROOT/"business-service/web/index.html").read_text(encoding="utf-8")
            script=html.split("<script>",1)[1].split("</script>",1)[0]
            script_path=temp/"admin.js"
            script_path.write_text(script,encoding="utf-8")
            subprocess.run(["node","--check",str(script_path)],check=True)
            service=subprocess.Popen([str(BINARY)],env=env,stdout=log,stderr=log)
            try:
                for _ in range(100):
                    try:
                        if call(base,"/health")["status"]=="ok":break
                    except (OSError,urllib.error.URLError):time.sleep(.1)
                else:raise AssertionError("service did not start")
                try:call(base,"/api/catalog")
                except urllib.error.HTTPError as error:assert error.code==401
                else:raise AssertionError("catalog auth missing")
                industry=call(base,"/api/catalog/industries",{"name":"装修装饰"},key)["id"]
                project=call(base,"/api/catalog/projects",{"industry_id":industry,
                    "name":"老房翻新","question":"您想做局部改造还是整屋翻新？"},key)["id"]
                assert call(base,"/api/catalog/public")=={"industries":[],"projects":[]}
                call(base,f"/api/catalog/industries/{industry}/publish",{},key)
                assert call(base,"/api/catalog/public")["projects"]==[]
                call(base,f"/api/catalog/projects/{project}/publish",{},key)
                published=call(base,"/api/catalog/public")
                assert published["industries"][0]["name"]=="装修装饰"
                assert published["projects"][0]["question"]=="您想做局部改造还是整屋翻新？"
                call(base,"/api/catalog/projects",{"id":project,"industry_id":industry,
                    "name":"老房翻新","question":"您的房屋面积大概是多少？"},key)
                assert call(base,"/api/catalog/public")["projects"][0]["question"]=="您想做局部改造还是整屋翻新？"
                call(base,f"/api/catalog/projects/{project}/publish",{},key)
                assert call(base,"/api/catalog/public")["projects"][0]["question"]=="您的房屋面积大概是多少？"
                other=call(base,"/api/catalog/industries",{"name":"教育教培"},key)["id"]
                try:call(base,"/api/catalog/projects",{"id":project,"industry_id":other,
                    "name":"老房翻新","question":"测试？"},key)
                except urllib.error.HTTPError as error:assert error.code==409
                else:raise AssertionError("project moved between industries")
                call(base,f"/api/catalog/projects/{project}",{},key,"DELETE")
                assert call(base,"/api/catalog/public")["projects"]==[]
                call(base,f"/api/catalog/industries/{industry}",{},key,"DELETE")
                assert all(item["name"]!="装修装饰" for item in call(base,"/api/catalog/public")["industries"])
                print(json.dumps({"result":"passed","checks":["admin auth","draft isolation",
                    "published project question","no cross-industry project move","unused catalog deletion",
                    "admin JavaScript syntax"]},ensure_ascii=False))
            finally:
                service.terminate()
                service.wait(timeout=10)

if __name__=="__main__": main()
