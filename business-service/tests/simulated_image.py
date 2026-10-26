"""Targeted image-message check with an isolated service and FastGPT stub."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg=="
)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    root = Path(__file__).resolve().parents[2]
    binary = root / "business-service/target/debug/douyin-chatbot-service.exe"
    key = "simulated-image-targeted-key"
    seen = []

    class FastGPT(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            seen.append(json.loads(self.rfile.read(length)))
            decision = {"reply": "看到了图片，请问您想了解哪方面？", "action": "reply", "fields": {}}
            body = json.dumps({"choices": [{"message": {"content": json.dumps(decision)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    fake = ThreadingHTTPServer(("127.0.0.1", free_port()), FastGPT)
    thread = threading.Thread(target=fake.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="simulated-image-", ignore_cleanup_errors=True) as temp:
            database = Path(temp) / "service.sqlite"
            accounts = Path(temp) / "accounts.json"
            accounts.write_text("[]", encoding="utf-8")
            base = f"http://127.0.0.1:{free_port()}"
            env = {**os.environ, "BIND": base.removeprefix("http://"), "DATABASE_PATH": str(database),
                   "BUSINESS_API_KEY": key, "INTERNAL_API_KEY": "simulated-image-internal-key",
                   "ACCOUNTS_FILE": str(accounts), "SEND_MODE": "mock",
                   "FASTGPT_URL": f"http://127.0.0.1:{fake.server_port}", "FASTGPT_APP_KEY": "stub-app-key"}
            with open(Path(temp) / "service.log", "w", encoding="utf-8") as log:
                service = subprocess.Popen([str(binary)], env=env, stdout=log, stderr=log)
                try:
                    for _ in range(100):
                        if service.poll() is not None:
                            raise AssertionError(Path(temp, "service.log").read_text(encoding="utf-8"))
                        try:
                            urllib.request.urlopen(base + "/health", timeout=1).close()
                            break
                        except (urllib.error.URLError, TimeoutError):
                            time.sleep(.1)
                    else:
                        raise AssertionError("service did not start")

                    with sqlite3.connect(database) as db:
                        db.execute("INSERT INTO merchants(id,created_at) VALUES('merchant-one',0)")
                        db.execute("INSERT INTO merchant_accounts(id,merchant_id,douyin_nickname,industry,fastgpt_app_id,created_at) VALUES('101','merchant-one','测试商家','家政服务','workflow-one',0)")

                    def call(path, body=None, authorized=True):
                        headers = {"Content-Type": "application/json"}
                        if authorized:
                            headers["x-api-key"] = key
                        request = urllib.request.Request(base + path,
                            data=json.dumps(body).encode() if body is not None else None,
                            headers=headers)
                        try:
                            with urllib.request.urlopen(request, timeout=5) as response:
                                return response.status, response.headers, response.read()
                        except urllib.error.HTTPError as error:
                            return error.code, error.headers, error.read()

                    image = "data:image/png;base64," + base64.b64encode(PNG).decode()
                    message = {"event_id": "evt-one", "message_id": "msg-one", "conversation_id": "chat-one",
                               "customer_id": "customer-one", "customer_nickname": "测试客户", "text": "这张图可以做吗 😊",
                               "image": image, "direction": "customer"}
                    status, _, body = call("/api/simulate/101", message)
                    assert status == 200, body
                    result = json.loads(body)
                    event, conversation = result["id"], result["conversation_id"]

                    status, _, body = call(f"/api/admin/pages/history/{conversation}?page=1")
                    history = json.loads(body)
                    assert status == 200 and history["items"][0]["has_image"] == 1
                    assert "image_data" not in history["items"][0]
                    assert history["items"][0]["text"] == message["text"]
                    assert call(f"/api/admin/images/{event}", authorized=False)[0] == 401
                    status, headers, body = call(f"/api/admin/images/{event}")
                    assert status == 200 and headers["Content-Type"] == "image/png" and body == PNG
                    assert headers["Cache-Control"] == "private, no-store"

                    for _ in range(100):
                        if seen:
                            break
                        time.sleep(.1)
                    assert seen, Path(temp, "service.log").read_text(encoding="utf-8")
                    sent = seen[0]
                    content = sent["messages"][0]["content"]
                    assert sent["appId"] == "workflow-one"
                    assert content[0] == {"type": "text", "text": message["text"]}
                    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"] == image

                    status, _, body = call("/api/simulate/101", message)
                    assert status == 200 and json.loads(body)["duplicate"] is True
                    changed = {**message, "image": "data:image/jpeg;base64," + base64.b64encode(PNG).decode()}
                    assert call("/api/simulate/101", changed)[0] == 422

                    image_only = {**message, "event_id": "evt-two", "message_id": "msg-two", "text": ""}
                    status, _, body = call("/api/simulate/101", image_only)
                    assert status == 200, body
                    for _ in range(100):
                        if len(seen) >= 2:
                            break
                        time.sleep(.1)
                    assert len(seen) >= 2
                    assert seen[1]["messages"][0]["content"][0] == {"type": "text", "text": "[图片]"}

                    print("simulated image: storage, protected display, history, duplicate, text+image and image-only FastGPT payload passed")
                finally:
                    service.terminate()
                    service.wait(timeout=10)
    finally:
        fake.shutdown()
        fake.server_close()


if __name__ == "__main__":
    main()
