"""Focused lead search, export, and expiring transcript-link integration check."""

from io import BytesIO
from contextlib import closing
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
from urllib.parse import urlencode, urlparse, parse_qs
import hashlib

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "business-service/target/debug/douyin-chatbot-service.exe"
KEY = "lead-export-check-key-1234567890"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(base, path, body=None, authenticated=True):
    headers = {"Content-Type": "application/json"}
    if authenticated:
        headers["x-api-key"] = KEY
    req = urllib.request.Request(base + path,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read(), response.headers


def data(base, path, body=None, authenticated=True):
    return json.loads(request(base, path, body, authenticated)[0])


def main():
    assert BINARY.exists(), "build the business-service debug binary first"
    with tempfile.TemporaryDirectory(prefix="douyin-lead-export-") as folder:
        temp = Path(folder)
        database = temp / "business.sqlite"
        (temp / "accounts.json").write_text("[]", encoding="utf-8")
        base = f"http://127.0.0.1:{free_port()}"
        env = {**os.environ, "BUSINESS_API_KEY": KEY,
               "INTERNAL_API_KEY": "lead-export-internal-1234567890",
               "ACCOUNTS_FILE": str(temp / "accounts.json"),
               "DATABASE_PATH": str(database), "BIND": base.removeprefix("http://"),
               "BUSINESS_URL": base, "SEND_MODE": "mock", "ADMIN_TOOLS_AUTO_LOGIN": "false"}
        with (temp / "service.log").open("w", encoding="utf-8") as log:
            service = subprocess.Popen([str(BINARY)], env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        if data(base, "/health")["status"] == "ok":
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError("service failed to start")
                with closing(sqlite3.connect(database)) as db, db:
                    db.create_function("unixepoch", 0, lambda: int(time.time()))
                    db.execute("INSERT INTO merchants(id,current_version_id) VALUES('merchant','version')")
                    db.execute("INSERT INTO merchant_versions(id,merchant_id,version,status,profile_json,contact_name,contact_phone) VALUES(?,?,?,?,?,?,?)",
                               ("version", "merchant", 1, "approved", json.dumps({"fields": {"完整门店名称": "泰安直达灯塔语言培训有限公司"}}, ensure_ascii=False), "张先生", "13800000000"))
                    db.execute("INSERT INTO merchant_accounts(id,merchant_id,douyin_nickname,industry,fastgpt_app_id) VALUES(?,?,?,?,?)",
                               ("12345678912", "merchant", "泰安小帅聊雅思", "教育教培", "app"))
                    for index, (conversation, lead_value) in enumerate([
                        ("conversation-one", "15864761053"),
                        ("conversation-two", "=1+1+1"),
                    ], start=1):
                        db.execute("INSERT INTO conversations(id,account_id,external_id,customer_id) VALUES(?,?,?,?)",
                                   (conversation, "12345678912", "模拟会话" + str(index), "customer" + str(index)))
                        db.execute("INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status) VALUES(?,?,?,?,?,?,?,?)",
                                   ("event" + str(index), "12345678912", "source" + str(index), "message" + str(index), conversation,
                                    "你好，<script>alert(1)</script> 我的联系方式" + lead_value, 1, "processed"))
                        db.execute("INSERT INTO outbox(id,event_id,conversation_id,revision,text,status,sent_at) VALUES(?,?,?,?,?,'mock_sent',unixepoch())",
                                   ("outbox" + str(index), "event" + str(index), conversation, 1, "收到，稍后店长联系您详聊。"))
                        if index == 1:
                            db.execute("INSERT INTO outbox(id,event_id,conversation_id,revision,text,status,sent_at,part_index) VALUES(?,?,?,?,?,'mock_sent',unixepoch(),1)",
                                       ("outbox1-second", "event1", conversation, 1, "这是同轮生成的第二条回复。"))
                        db.execute("INSERT INTO leads(id,conversation_id,event_id,kind,value) VALUES(?,?,?,?,?)",
                                   ("lead" + str(index), conversation, "event" + str(index), "phone" if index == 1 else "wechat", lead_value))
                    db.commit()

                for search in ("1234567", "小帅聊雅思", "直达灯塔"):
                    rows = data(base, "/api/leads?" + urlencode({"search": search, "exported": "no"}))["items"]
                    assert len(rows) == 2 and rows[0]["store_name"] == "泰安直达灯塔语言培训有限公司", (search, rows)
                assert data(base, "/api/leads?exported=yes")["total"] == 0
                try:
                    request(base, "/api/leads", authenticated=False)
                    raise AssertionError("unauthorized lead listing was allowed")
                except urllib.error.HTTPError as error:
                    assert error.code == 401

                link = data(base, "/api/leads/lead1/history-link", {})
                assert 604790 <= link["expires_at"] - int(time.time()) <= 604800
                token = parse_qs(urlparse(link["url"]).query)["token"][0]
                page = request(base, "/lead-history?token=" + token, authenticated=False)[0].decode("utf-8")
                assert "模拟会话1" in page and "收到，稍后店长联系您详聊。" in page
                assert page.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 1
                assert page.index("收到，稍后店长联系您详聊。") < page.index("这是同轮生成的第二条回复。")
                assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert(1)</script>" not in page
                assert "模拟会话2" not in page and "=1+1+1" not in page
                with closing(sqlite3.connect(database)) as db, db:
                    db.create_function("unixepoch", 0, lambda: int(time.time()))
                    db.execute("UPDATE lead_history_links SET expires_at=unixepoch()-1 WHERE token_hash=?",
                               (hashlib.sha256(token.encode()).hexdigest(),))
                    db.commit()
                try:
                    request(base, "/lead-history?token=" + token, authenticated=False)
                    raise AssertionError("expired link was allowed")
                except urllib.error.HTTPError as error:
                    assert error.code == 404

                content, headers = request(base, "/api/leads/export", {"ids": ["lead1", "lead2"]})
                assert "spreadsheetml.sheet" in headers["Content-Type"]
                sheet = load_workbook(BytesIO(content)).active
                values = list(sheet.values)
                assert len(values) == 3 and values[0] == ("类型", "联系字段", "会话记录链接"), values
                assert values[1][0:2] == ("电话", "15864761053") and values[2][0:2] == ("微信", "=1+1+1"), values
                assert values[1][2].startswith(base + "/lead-history?token=")
                assert values[2][2].startswith(base + "/lead-history?token=")
                assert sheet["C2"].hyperlink.target == values[1][2]
                assert sheet["C3"].hyperlink.target == values[2][2]
                assert data(base, "/api/leads?exported=yes")["total"] == 2
                assert data(base, "/api/leads?exported=no")["total"] == 0
                print(json.dumps({"result": "passed", "checks": [
                    "search by Douyin ID/nickname/store", "exported-state filter",
                    "seven-day token and expiry", "transcript escaping and conversation isolation",
                    "three-column XLSX with literal contact strings", "export state updated"]}, ensure_ascii=False))
            finally:
                service.terminate()
                service.wait(timeout=10)


if __name__ == "__main__":
    main()
