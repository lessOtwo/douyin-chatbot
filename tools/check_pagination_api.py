"""Check bounded admin pages and exact totals beyond the old 100-row cap."""

from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "business-service/target/debug/douyin-chatbot-service.exe"
KEY = "pagination-check-key-1234567890"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get(base, route):
    request = urllib.request.Request(base + route, headers={"x-api-key": KEY})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main():
    assert BINARY.exists(), "build the business-service debug binary first"
    with tempfile.TemporaryDirectory(prefix="douyin-pagination-") as folder:
        temp = Path(folder)
        database = temp / "business.sqlite"
        (temp / "accounts.json").write_text("[]", encoding="utf-8")
        base = f"http://127.0.0.1:{port()}"
        env = {**os.environ, "BUSINESS_API_KEY": KEY,
               "INTERNAL_API_KEY": "pagination-internal-1234567890",
               "ACCOUNTS_FILE": str(temp / "accounts.json"),
               "DATABASE_PATH": str(database), "BIND": base.removeprefix("http://"),
               "BUSINESS_URL": base, "SEND_MODE": "mock", "ADMIN_TOOLS_AUTO_LOGIN": "false"}
        with (temp / "service.log").open("w", encoding="utf-8") as log:
            service = subprocess.Popen([str(BINARY)], env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        if get(base, "/health")["status"] == "ok":
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError("service failed to start")
                with closing(sqlite3.connect(database)) as db, db:
                    db.create_function("unixepoch", 0, lambda: int(time.time()))
                    db.execute("INSERT INTO merchants(id) VALUES('merchant')")
                    db.execute("INSERT INTO merchant_accounts(id,merchant_id,douyin_nickname,industry,fastgpt_app_id) VALUES(?,?,?,?,?)",
                               ("account", "merchant", "商家", "教育教培", "app"))
                    for index in range(125):
                        suffix = str(index)
                        db.execute("INSERT INTO conversations(id,account_id,external_id,customer_id) VALUES(?,?,?,?)",
                                   ("conv" + suffix, "account", "chat" + suffix, "customer" + suffix))
                        db.execute("INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status) VALUES(?,?,?,?,?,?,?,?)",
                                   ("event" + suffix, "account", "source" + suffix, "message" + suffix,
                                    "conv" + suffix, "内容" + suffix, 1, "blocked"))
                        db.execute("INSERT INTO outbox(id,event_id,conversation_id,revision,text,status) VALUES(?,?,?,?,?,?)",
                                   ("outbox" + suffix, "event" + suffix, "conv" + suffix, 1, "回复" + suffix, "mock_sent"))
                        db.execute("INSERT INTO leads(id,conversation_id,event_id,kind,value) VALUES(?,?,?,?,?)",
                                   ("lead" + suffix, "conv" + suffix, "event" + suffix, "phone", "138" + suffix.zfill(8)))
                        db.execute("INSERT INTO unanswered_questions(id,event_id,conversation_id,reason) VALUES(?,?,?,?)",
                                   ("question" + suffix, "event" + suffix, "conv" + suffix, "模拟问题"))
                        db.execute("INSERT INTO merchant_invitations(id,token_hash,status,expires_at) VALUES(?,?,'revoked',unixepoch()+3600)",
                                   ("invite" + suffix, "token" + suffix))
                        db.execute("INSERT INTO merchant_versions(id,merchant_id,version,status,profile_json,contact_name,contact_phone) VALUES(?,?,?,?,?,?,?)",
                                   ("version" + suffix, "merchant", index + 1, "approved", "{}", "联系人", "13800000000"))
                        db.execute("INSERT INTO industries(id,draft_name,draft_question) VALUES(?,?,?)",
                                   ("industry" + suffix, "行业" + suffix, ""))
                        db.execute("INSERT INTO industry_projects(id,industry_id,draft_name,draft_question) VALUES(?,?,?,?)",
                                   ("project" + suffix, "industry" + suffix, "项目" + suffix, "问句"))
                        if index:
                            db.execute("INSERT INTO merchants(id) VALUES(?)", ("merchant" + suffix,))
                            db.execute("INSERT INTO merchant_accounts(id,merchant_id,douyin_nickname,industry,fastgpt_app_id) VALUES(?,?,?,?,?)",
                                       ("account" + suffix, "merchant" + suffix, "商家" + suffix, "教育教培", "app"))
                    db.commit()
                snapshot = get(base, "/api/snapshot")
                assert all(len(snapshot[name]) == 100 for name in ("conversations", "events", "outbox", "leads"))
                assert all(snapshot["totals"][name] == 125 for name in ("conversations", "events", "outbox", "leads"))
                for kind in ("conversations", "outbox", "errors", "accounts", "industries", "projects", "invitations", "merchants"):
                    first = get(base, f"/api/admin/pages/{kind}?page=1")
                    last = get(base, f"/api/admin/pages/{kind}?page=7")
                    assert (first["total"], first["page"], len(first["items"])) == (125, 1, 20), kind
                    assert (last["total"], last["page"], len(last["items"])) == (125, 7, 5), kind
                leads = get(base, "/api/leads?exported=all&page=7")
                questions = get(base, "/api/unanswered?status=open&page=7")
                assert (leads["total"], leads["page"], len(leads["items"])) == (125, 7, 5)
                assert (questions["total"], questions["page"], len(questions["items"])) == (125, 7, 5)
                assert get(base, "/api/leads?search=" + quote("商家") + "&page=1")["total"] == 125
                assert get(base, "/api/leads?exported=yes&page=5")["page"] == 1
                project_matches = get(base, "/api/admin/pages/projects?search=" + quote("行业1"))["total"]
                assert project_matches == sum(str(i).startswith("1") for i in range(125)), project_matches
                history = get(base, "/api/admin/pages/history/conv1?page=1")
                assert (history["total"], len(history["items"])) == (1, 1)
                print(json.dumps({"result": "passed", "checks": ["exact counts", "bounded pages", "last page", "filters", "history"]}))
            finally:
                service.terminate()
                service.wait(timeout=10)


if __name__ == "__main__":
    main()
