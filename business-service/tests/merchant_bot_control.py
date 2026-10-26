"""Targeted merchant-wide pause check against an isolated local service."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    root = Path(__file__).resolve().parents[2]
    binary = root / "business-service/target/debug/douyin-chatbot-service.exe"
    key = "merchant-pause-targeted-check"
    with tempfile.TemporaryDirectory(prefix="merchant-bot-control-", ignore_cleanup_errors=True) as temp:
        database = Path(temp) / "service.sqlite"
        # Reproduce an existing installation whose merchants table predates the switch.
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE merchants (id TEXT PRIMARY KEY, current_version_id TEXT, created_at INTEGER NOT NULL DEFAULT (unixepoch()))")
        base = f"http://127.0.0.1:{free_port()}"
        env = {**os.environ, "BIND": base.removeprefix("http://"), "DATABASE_PATH": str(database),
               "BUSINESS_API_KEY": key, "INTERNAL_API_KEY": "merchant-pause-internal-check",
               "ACCOUNTS_FILE": str(root / "config/accounts.json"),
               "SEND_MODE": "mock"}
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
                    raise AssertionError(Path(temp, "service.log").read_text(encoding="utf-8"))

                def call(path, body=None):
                    request = urllib.request.Request(base + path,
                        data=json.dumps(body).encode() if body is not None else None,
                        headers={"x-api-key": key, "Content-Type": "application/json"})
                    try:
                        with urllib.request.urlopen(request, timeout=5) as response:
                            return response.status, json.load(response)
                    except urllib.error.HTTPError as error:
                        return error.code, json.load(error)

                with sqlite3.connect(database) as db:
                    db.execute("INSERT INTO merchants(id,created_at) VALUES('merchant-one',0)")
                    db.execute("INSERT INTO merchants(id,created_at) VALUES('merchant-two',0)")
                    db.execute("INSERT INTO merchant_accounts(id,merchant_id,douyin_nickname,industry,created_at) VALUES('101','merchant-one','测试商家','家政服务',0)")
                    db.execute("INSERT INTO merchant_accounts(id,merchant_id,douyin_nickname,industry,created_at) VALUES('202','merchant-two','其他商家','教育教培',0)")

                def incoming(account, suffix, conversation="old"):
                    return call(f"/webhooks/platform/{account}", {
                        "event_id": f"event-{account}-{suffix}", "message_id": f"message-{account}-{suffix}",
                        "conversation_id": conversation, "customer_id": "customer", "text": "你好"})

                assert call("/api/merchants/missing/bot-control", {"paused": True})[0] == 404
                assert incoming("101", "before")[0] == 200
                assert call("/api/merchants/merchant-one/bot-control", {"paused": True})[1]["changed"]
                with sqlite3.connect(database) as db:
                    assert db.execute("SELECT bot_paused FROM merchants WHERE id='merchant-one'").fetchone()[0] == 1
                    assert db.execute("SELECT mode FROM conversations WHERE account_id='101'").fetchone()[0] == "bot"
                assert incoming("101", "existing")[1]["status"] == "held"
                assert incoming("101", "future", "new")[1]["status"] == "held"
                with sqlite3.connect(database) as db:
                    conversation, revision = db.execute("SELECT id,revision FROM conversations WHERE account_id='101' AND external_id='new'").fetchone()
                    db.execute("INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status,created_at,direction) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               ("queued-check-event", "101", "queued-check-source", "queued-check-message", conversation, "你好", revision, "processed", 0, "customer"))
                    db.execute("INSERT INTO outbox(id,event_id,conversation_id,revision,text,status,created_at,source,part_index) VALUES(?,?,?,?,?,?,?,?,?)",
                               ("queued-check-reply", "queued-check-event", conversation, revision, "不应发送", "queued", 0, "bot_handoff", 0))
                for _ in range(30):
                    with sqlite3.connect(database) as db:
                        queued_status = db.execute("SELECT status FROM outbox WHERE id='queued-check-reply'").fetchone()[0]
                    if queued_status != "queued":
                        break
                    time.sleep(.1)
                assert queued_status == "cancelled"
                assert incoming("202", "other")[1]["status"] == "pending"
                status, result = call("/api/admin/options/merchants")
                assert status == 200 and next(m for m in result["items"] if m["merchant_id"] == "merchant-one")["bot_paused"] == 1
                assert call("/api/merchants/merchant-one/bot-control", {"paused": False})[1]["changed"]
                assert incoming("101", "resumed")[1]["status"] == "pending"
                with sqlite3.connect(database) as db:
                    conversation = db.execute("SELECT id FROM conversations WHERE account_id='101' AND external_id='old'").fetchone()[0]
                assert call(f"/api/conversations/{conversation}/control", {"mode": "paused"})[0] == 200
                assert call("/api/merchants/merchant-one/bot-control", {"paused": True})[0] == 200
                assert call("/api/merchants/merchant-one/bot-control", {"paused": False})[0] == 200
                assert incoming("101", "independent-pause")[1]["status"] == "held"
                print("merchant-wide pause: migration, existing/new conversations, isolation, resume and per-conversation pause passed")
            finally:
                service.terminate()
                service.wait(timeout=10)


if __name__ == "__main__":
    main()
