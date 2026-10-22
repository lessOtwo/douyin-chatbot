"""Targeted adapter/UI-auth checks; no upstream suites or external message sending."""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(base, key):
    def call(path, payload=None, auth=True, extra=None, opener=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["x-api-key"] = key
        headers.update(extra or {})
        request = urllib.request.Request(base + path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            response = opener.open(request, timeout=10) if opener else urllib.request.urlopen(request, timeout=10)
            with response:
                content = response.read().decode()
                return response.status, json.loads(content) if content.startswith("{") else content
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def snapshot():
        return call("/api/snapshot")[1]

    def wait(predicate):
        for _ in range(100):
            current = snapshot()
            if predicate(current):
                return current
            time.sleep(.1)
        raise AssertionError("targeted check timed out: " + json.dumps(current, ensure_ascii=False))

    prefix = "smoke-" + str(uuid.uuid4())
    event = {"event_id": prefix, "message_id": prefix, "conversation_id": prefix,
             "customer_id": "controlled-test-user", "text": "我想咨询老房翻新"}
    assert call("/api/snapshot", auth=False)[0] == 401
    assert call("/api/accounts", auth=False)[0] == 401
    assert call("/")[0] == 200
    assert call("/api/login", {"key": key}, auth=False, extra={"Origin": "https://wrong.invalid"})[0] == 403
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    assert call("/api/login", {"key": key}, auth=False, extra={"Origin": base}, opener=browser)[0] == 200
    assert call("/api/snapshot", auth=False, opener=browser)[0] == 200
    status, accepted = call("/webhooks/platform/renovation-demo", event)
    assert status == 200 and accepted["status"] == "pending"
    cid = accepted["conversation_id"]
    current = wait(lambda s: any(o["event_id"] == accepted["id"] and o["status"] == "mock_sent" for o in s["outbox"]))
    assert call("/webhooks/platform/renovation-demo", event)[1]["duplicate"]
    assert call("/webhooks/platform/renovation-demo", {**event, "text": "conflicting content"})[0] == 409
    assert len([o for o in current["outbox"] if o["event_id"] == accepted["id"]]) == 1
    session = next(c for c in current["conversations"] if c["id"] == cid)["session_id"]
    assert session
    second = {**event, "event_id": prefix+"-2", "message_id": prefix+"-2", "text": "不留电话，先说费用"}
    reply = call("/webhooks/platform/renovation-demo", second)[1]
    current = wait(lambda s: any(o["event_id"] == reply["id"] and o["status"] == "mock_sent" for o in s["outbox"]))
    assert next(c for c in current["conversations"] if c["id"] == cid)["session_id"] == session
    assert call("/api/conversations/"+cid+"/control", {"mode":"human"}, auth=False, extra={"Origin":"https://wrong.invalid"}, opener=browser)[0] == 403
    slow = {**event,"event_id":prefix+"-slow","message_id":prefix+"-slow","text":"slow-fixture 老房"}
    slow_id = call("/webhooks/platform/renovation-demo", slow)[1]["id"]
    wait(lambda s: any(e["id"]==slow_id and e["status"]=="processing" for e in s["events"]))
    assert call("/api/conversations/"+cid+"/control", {"mode":"human"})[0] == 200
    current = wait(lambda s: any(e["id"]==slow_id and e["status"]=="processed" for e in s["events"]))
    assert not any(o["event_id"]==slow_id for o in current["outbox"])
    assert call("/webhooks/platform/renovation-demo", {**event,"event_id":prefix+"-held","message_id":prefix+"-held"})[1]["status"] == "held"
    assert call("/api/conversations/"+cid+"/control", {"mode":"bot"})[0] == 200
    resumed = call("/webhooks/platform/renovation-demo", {**slow,"event_id":prefix+"-resume","message_id":prefix+"-resume"})[1]
    wait(lambda s: any(e["id"]==resumed["id"] and e["status"]=="processing" for e in s["events"]))
    assert call("/api/conversations/"+cid+"/control", {"mode":"human"})[0] == 200
    assert call("/api/conversations/"+cid+"/control", {"mode":"bot"})[0] == 200
    current = wait(lambda s: any(e["id"]==resumed["id"] and e["status"]=="processed" for e in s["events"]))
    assert next(c for c in current["conversations"] if c["id"]==cid)["session_id"] is None
    assert not any(o["event_id"]==resumed["id"] for o in current["outbox"])
    fresh = call("/webhooks/platform/renovation-demo", {**event,"event_id":prefix+"-fresh","message_id":prefix+"-fresh"})[1]
    current = wait(lambda s: any(o["event_id"]==fresh["id"] and o["status"]=="mock_sent" for o in s["outbox"]))
    assert next(c for c in current["conversations"] if c["id"]==cid)["session_id"] not in {None,session}
    contact = call("/webhooks/platform/renovation-demo", {**event,"event_id":prefix+"-contact","message_id":prefix+"-contact","text":"我的电话是13800000000"})[1]
    current = wait(lambda s: any(o["event_id"]==contact["id"] and o["status"]=="mock_sent" for o in s["outbox"]))
    assert any(l["event_id"]==contact["id"] and l["value"]=="13800000000" and l["status"]=="candidate" for l in current["leads"])
    stop = call("/webhooks/platform/renovation-demo", {**event,"event_id":prefix+"-stop","message_id":prefix+"-stop","text":"不要联系我"})[1]
    current = wait(lambda s: any(o["event_id"]==stop["id"] and o["status"]=="mock_sent" for o in s["outbox"]))
    assert next(c for c in current["conversations"] if c["id"]==cid)["mode"]=="human"
    assert call("/webhooks/platform/renovation-demo", {**event,"event_id":prefix+"-after-stop","message_id":prefix+"-after-stop"})[1]["status"]=="held"
    other = call("/webhooks/platform/second-account", event)[1]
    assert other["conversation_id"] != cid
    wait(lambda s: any(o["event_id"]==other["id"] and o["status"]=="mock_sent" for o in s["outbox"]))
    assert call("/webhooks/platform/renovation-demo",{**event,"event_id":prefix+"-conflict-user","message_id":prefix+"-conflict-user","customer_id":"another-user"})[0] == 409
    print(json.dumps({"result":"passed","scope":["admin login and CSRF","callback and Typebot-FastGPT contract","session mapping","duplicates and conflicts","takeover during generation","resume cannot restore stale session","contact candidate with source","stop automation on request","account isolation"],"real_typebot":False,"real_fastgpt":False,"real_douyin":False},ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url")
    parser.add_argument("--binary")
    args = parser.parse_args()
    if args.url:
        run(args.url, os.environ["BUSINESS_API_KEY"])
    else:
        root = Path(__file__).resolve().parents[2]
        binary = Path(args.binary) if args.binary else root/"business-service/target/debug/douyin-chatbot-service.exe"
        base, engine = "http://127.0.0.1:"+str(port()), "http://127.0.0.1:"+str(port())
        key, internal = "targeted-smoke-key-"+str(uuid.uuid4()), "targeted-internal-"+str(uuid.uuid4())
        with tempfile.TemporaryDirectory(prefix="douyin-chatbot-smoke-") as temp:
            runtime_env = {**os.environ,"BUSINESS_URL":base,"MOCK_PORT":engine.rsplit(":",1)[1],"INTERNAL_API_KEY":internal,
                           "BUSINESS_API_KEY":key,"BIND":base.removeprefix("http://"),"DATABASE_PATH":str(Path(temp)/"test.sqlite"),
                           "ACCOUNTS_FILE":str(root/"config/accounts.mock.json"),"TYPEBOT_URL":engine,"FASTGPT_URL":engine,"FASTGPT_APP_KEY":"mock-key","SEND_MODE":"mock"}
            with open(Path(temp)/"logs.txt","w",encoding="utf-8") as logs:
                processes = [subprocess.Popen([os.sys.executable,str(Path(__file__).with_name("mock_engines.py"))],env=runtime_env,stdout=logs,stderr=logs),
                             subprocess.Popen([str(binary)],env=runtime_env,stdout=logs,stderr=logs)]
                try:
                    for _ in range(100):
                        try:
                            urllib.request.urlopen(base+"/health",timeout=1).close()
                            break
                        except Exception:
                            time.sleep(.1)
                    run(base,key)
                finally:
                    for process in processes:
                        process.terminate()
                        process.wait(timeout=10)
