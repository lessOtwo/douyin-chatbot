"""Focused deletion check with two isolated merchants and a configured fallback account."""
import json
import os
from contextlib import closing, contextmanager
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "business-service/target/debug/douyin-chatbot-service.exe"
KEY = "delete-merchant-check-key-1234567890"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def db_session(path):
    with closing(sqlite3.connect(path)) as db:
        db.create_function("unixepoch", 0, lambda: int(time.time()))
        with db:
            yield db


def main():
    with tempfile.TemporaryDirectory(prefix="delete-merchant-", ignore_cleanup_errors=True) as folder:
        folder = Path(folder)
        database = folder / "business.sqlite"
        accounts = folder / "accounts.json"
        accounts.write_text(json.dumps([{"id": "303", "merchant_id": "target", "industry": "教育教培",
            "fastgpt_app_id": "workflow", "fastgpt_key_env": "FASTGPT_APP_KEY"}]), encoding="utf-8")
        base = f"http://127.0.0.1:{free_port()}"
        env = {**os.environ, "BIND": base.removeprefix("http://"),
            "DATABASE_PATH": str(database), "ACCOUNTS_FILE": str(accounts),
            "BUSINESS_API_KEY": KEY, "INTERNAL_API_KEY": "delete-merchant-internal-key-12345",
            "SEND_MODE": "mock"}
        with (folder / "service.log").open("w", encoding="utf-8") as log:
            service = subprocess.Popen([str(BINARY)], env=env, stdout=log, stderr=log)
            try:
                def call(path, method="GET", body=None, auth=True):
                    request = urllib.request.Request(base + path, method=method,
                        data=None if body is None else json.dumps(body).encode(),
                        headers={"Content-Type": "application/json", **({"x-api-key": KEY} if auth else {})})
                    try:
                        with urllib.request.urlopen(request, timeout=5) as response:
                            return response.status, json.load(response)
                    except urllib.error.HTTPError as error:
                        body = error.read().decode()
                        return error.code, json.loads(body) if body.startswith("{") else body

                for _ in range(100):
                    if service.poll() is not None:
                        raise AssertionError((folder / "service.log").read_text(encoding="utf-8"))
                    try:
                        if call("/health")[0] == 200:
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError("service did not start")

                with db_session(database) as db:
                    db.execute("PRAGMA foreign_keys=ON")
                    for merchant in ("target", "other"):
                        db.execute("INSERT INTO merchants(id) VALUES(?)", (merchant,))
                    for version, merchant in (("v1", "target"), ("v2", "target"), ("v3", "other")):
                        db.execute("INSERT INTO merchant_versions(id,merchant_id,version,status,profile_json,contact_name,contact_phone) VALUES(?,?,?,?,?,?,?)",
                            (version, merchant, int(version[-1]), "approved",
                             json.dumps({"fields": {"完整门店名称": merchant}}, ensure_ascii=False), "联系人", "12345678901"))
                    for account, merchant in (("101", "target"), ("202", "other")):
                        db.execute("INSERT INTO merchant_accounts(id,merchant_id,douyin_nickname,industry,fastgpt_app_id) VALUES(?,?,?,?,?)",
                            (account, merchant, "昵称", "教育教培", "shared-workflow"))
                    for invitation, merchant in (("i1", "target"), ("i2", "other")):
                        db.execute("INSERT INTO merchant_invitations(id,token_hash,merchant_id,status,expires_at) VALUES(?,?,?,'used',9999999999)",
                            (invitation, invitation, merchant))
                    for account, conversation in (("101", "c1"), ("303", "c3"), ("202", "c2")):
                        db.execute("INSERT INTO conversations(id,account_id,external_id,customer_id) VALUES(?,?,?,?)",
                            (conversation, account, conversation, "customer"))
                        event = "e" + conversation[-1]
                        db.execute("INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status) VALUES(?,?,?,?,?,?,0,'processed')",
                            (event, account, event, event, conversation, "客户消息"))
                        db.execute("INSERT INTO decisions(event_id,result) VALUES(?,?)", (event, '{}'))
                        db.execute("INSERT INTO outbox(id,event_id,conversation_id,revision,text,status) VALUES(?,?,?,0,'回复','mock_sent')",
                            ("o" + conversation[-1], event, conversation))
                        db.execute("INSERT INTO leads(id,conversation_id,event_id,kind,value) VALUES(?,?,?,'wechat','abc123')",
                            ("l" + conversation[-1], conversation, event))
                        db.execute("INSERT INTO lead_history_links(token_hash,conversation_id,max_event_rowid,expires_at) VALUES(?,?,1,9999999999)",
                            ("h" + conversation[-1], conversation))
                        db.execute("INSERT INTO unanswered_questions(id,event_id,conversation_id,reason) VALUES(?,?,?,'未解答')",
                            ("q" + conversation[-1], event, conversation))
                    db.execute("UPDATE events SET status='processing' WHERE id='e1'")
                    db.execute("UPDATE outbox SET status='sending' WHERE id='o1'")
                    db.execute("UPDATE events SET status='uncertain',error='model failure' WHERE id='e3'")
                    db.execute("UPDATE outbox SET status='uncertain',error='send failure' WHERE id='o3'")

                assert call("/api/merchants/target", "DELETE", auth=False)[0] == 401
                assert call("/api/merchants/missing", "DELETE")[0] == 404
                assert call("/api/merchants/target", "DELETE")[0] == 409
                with db_session(database) as db:
                    assert db.execute("SELECT COUNT(*) FROM merchants WHERE id='target'").fetchone()[0] == 1
                    db.execute("UPDATE events SET status='uncertain' WHERE id='e1'")
                    db.execute("UPDATE outbox SET status='uncertain' WHERE id='o1'")

                status, result = call("/api/merchants/target", "DELETE")
                assert status == 200 and result["deleted"] and result["merchant_id"] == "target"
                with db_session(database) as db:
                    db.execute("PRAGMA foreign_keys=ON")
                    for table, condition in (
                        ("merchants", "id='target'"), ("merchant_accounts", "merchant_id='target'"),
                        ("merchant_versions", "merchant_id='target'"),
                        ("merchant_invitations", "merchant_id='target'"),
                        ("conversations", "account_id IN ('101','303')"),
                        ("events", "account_id IN ('101','303')"),
                        *[(table, "conversation_id IN ('c1','c3')") for table in
                            ("outbox", "leads", "lead_history_links", "unanswered_questions")],
                        ("decisions", "event_id IN ('e1','e3')"),
                    ):
                        assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE {condition}").fetchone()[0] == 0, table
                    for table in ("merchants", "merchant_accounts", "merchant_versions", "merchant_invitations",
                                  "conversations", "events", "decisions", "outbox", "leads",
                                  "lead_history_links", "unanswered_questions"):
                        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1, table
                    assert db.execute("SELECT COUNT(*) FROM removed_accounts WHERE id='303'").fetchone()[0] == 1
                    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
                assert call("/api/merchants/target", "DELETE")[0] == 404
                incoming = {"event_id": "after-delete", "message_id": "after-delete",
                    "conversation_id": "after-delete", "customer_id": "customer", "text": "你好"}
                assert call("/webhooks/platform/101", "POST", incoming)[0] == 404
                assert call("/webhooks/platform/303", "POST", incoming)[0] == 404
                assert [a["id"] for a in call("/api/accounts")[1]["accounts"]] == ["202"]
                assert "data-delete-merchant" in (ROOT / "business-service/web/index.html").read_text(encoding="utf-8")
                print("merchant deletion: auth, busy guard, scoped cascade, fallback account, foreign keys passed")
            finally:
                service.terminate()
                service.wait(timeout=10)


if __name__ == "__main__":
    main()
