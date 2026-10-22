"""Focused backend check for model-selected lead channels and later preference changes."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from threading import Thread
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "business-service/target/debug/douyin-chatbot-service.exe"
KEY = "lead-method-check-key-1234567890"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Model(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = payload["messages"][0]["content"]
        if text == "我是安装木门的，电话13500000000":
            decision = {"reply": "不用呢 谢谢", "action": "peer", "fields": {"phone": "13500000000"}}
            fields = None
        elif text == "别打电话，加我微信":
            fields = {"contact_method": "wechat", "contact_value": "13700000000"}
        elif text == "手机号13900000000，加我微信不要打电话":
            fields = {"phone": "13900000000", "contact_method": "wechat"}
        elif text == "手机号13600000000，加我微信":
            fields = {"phone": "13600000000", "contact_method": "wechat"}
        elif text == "微信号是abc12345":
            fields = {"wechat": "abc12345", "contact_method": "wechat"}
        elif text == "手机号13700000000，给我打电话":
            fields = {"phone": "13700000000", "contact_method": "phone"}
        else:
            raise AssertionError("unexpected model fixture input: " + text)
        if fields is not None:
            decision = {"reply": "收到，稍后店长联系您详聊。", "action": "reply", "fields": fields}
        data = json.dumps({"choices": [{"message": {"content": json.dumps(decision, ensure_ascii=False)}}]},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def call(base, path, body=None):
    request = urllib.request.Request(base + path,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": KEY})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def wait_for(base, conversation, expected):
    for _ in range(100):
        rows = [lead for lead in call(base, "/api/snapshot")["leads"]
                if lead["conversation_id"] == conversation]
        if sorted((row["kind"], row["value"]) for row in rows) == expected:
            return rows
        time.sleep(0.1)
    raise AssertionError(f"lead channel did not become {expected}: {rows}")


def main():
    assert BINARY.exists(), "build the business-service debug binary first"
    model_port = free_port()
    model = ThreadingHTTPServer(("127.0.0.1", model_port), Model)
    Thread(target=model.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{free_port()}"
    try:
        with tempfile.TemporaryDirectory(prefix="douyin-lead-method-") as folder:
            temp = Path(folder)
            (temp / "accounts.json").write_text(json.dumps([{
                "id": "demo", "merchant_id": "merchant-demo", "industry": "教育教培",
                "fastgpt_app_id": "fixture-app", "fastgpt_key_env": "FASTGPT_APP_KEY",
            }]), encoding="utf-8")
            env = {**os.environ, "BUSINESS_API_KEY": KEY,
                "INTERNAL_API_KEY": "lead-method-internal-1234567890",
                "ACCOUNTS_FILE": str(temp / "accounts.json"),
                "DATABASE_PATH": str(temp / "business.sqlite"),
                "BIND": base.removeprefix("http://"), "SEND_MODE": "mock",
                "FASTGPT_URL": f"http://127.0.0.1:{model_port}",
                "FASTGPT_APP_KEY": "fixture-key", "ADMIN_TOOLS_AUTO_LOGIN": "false"}
            with (temp / "service.log").open("w", encoding="utf-8") as log:
                service = subprocess.Popen([str(BINARY)], env=env, stdout=log, stderr=log)
                try:
                    for _ in range(100):
                        try:
                            if call(base, "/health")["status"] == "ok":
                                break
                        except OSError:
                            time.sleep(0.1)
                    else:
                        raise AssertionError("service failed to start")

                    def send(number, conversation, text):
                        return call(base, "/api/simulate/demo", {
                            "event_id": f"event-{number}", "message_id": f"message-{number}",
                            "conversation_id": conversation, "customer_id": "customer-" + conversation,
                            "text": text, "direction": "customer"})

                    direct = send(1, "phone", "手机号13700000000，给我打电话")
                    wait_for(base, direct["conversation_id"], [("phone", "13700000000")])
                    wechat = send(2, "wechat", "手机号13900000000，加我微信不要打电话")
                    wait_for(base, wechat["conversation_id"], [("wechat", "13900000000")])
                    explicit = send(3, "explicit", "微信号是abc12345")
                    wait_for(base, explicit["conversation_id"], [("wechat", "abc12345")])
                    changed = send(4, "changed", "手机号13700000000，给我打电话")
                    before = wait_for(base, changed["conversation_id"], [("phone", "13700000000")])[0]
                    send(5, "changed", "别打电话，加我微信")
                    after = wait_for(base, changed["conversation_id"], [("wechat", "13700000000")])[0]
                    assert after["id"] == before["id"] and after["event_id"] == before["event_id"]
                    another = send(6, "two-numbers", "手机号13700000000，给我打电话")
                    wait_for(base, another["conversation_id"], [("phone", "13700000000")])
                    send(7, "two-numbers", "手机号13600000000，加我微信")
                    wait_for(base, another["conversation_id"],
                             [("phone", "13700000000"), ("wechat", "13600000000")])
                    peer = call(base, "/api/simulate/demo", {
                        "event_id": "event-8", "message_id": "message-8",
                        "conversation_id": "peer", "customer_id": "customer-peer",
                        "customer_nickname": "木门安装服务商",
                        "text": "我是安装木门的，电话13500000000", "direction": "customer"})
                    for _ in range(100):
                        snapshot = call(base, "/api/snapshot")
                        row = next((c for c in snapshot["conversations"] if c["id"] == peer["conversation_id"]), None)
                        replies = [o for o in snapshot["outbox"] if o["conversation_id"] == peer["conversation_id"]]
                        if row and row["is_peer"] and replies:
                            break
                        time.sleep(0.1)
                    assert row["customer_nickname"] == "木门安装服务商" and row["is_peer"] == 1, row
                    assert replies[0]["text"] == "不用呢 谢谢", replies
                    assert not [lead for lead in snapshot["leads"] if lead["conversation_id"] == peer["conversation_id"]]
                    send(9, "peer", "手机号13700000000，给我打电话")
                    for _ in range(100):
                        snapshot = call(base, "/api/snapshot")
                        replies = [o for o in snapshot["outbox"] if o["conversation_id"] == peer["conversation_id"]]
                        if len(replies) == 2:
                            break
                        time.sleep(0.1)
                    assert len(replies) == 2 and all(o["text"] == "不用呢 谢谢" for o in replies), replies
                    assert not [lead for lead in snapshot["leads"] if lead["conversation_id"] == peer["conversation_id"]]
                    print(json.dumps({"result": "passed", "checks": [
                        "phone call", "phone used for WeChat search", "explicit WeChat ID",
                        "later channel preference updates existing candidate without losing source",
                        "a new number does not change the previous number's channel",
                        "peer reply remains fixed and creates no leads"]}))
                except Exception:
                    log.flush()
                    print((temp / "service.log").read_text(encoding="utf-8", errors="replace")[-3000:])
                    raise
                finally:
                    service.terminate()
                    service.wait(timeout=10)
    finally:
        model.shutdown()


if __name__ == "__main__":
    main()
