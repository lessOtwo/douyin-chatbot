"""Targeted migration, one-generation/two-send, and 50-round limit check."""

import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import uuid

from check_fastgpt_sessions import free_port, http, wait_for


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "business-service/target/debug/douyin-chatbot-service.exe"
BASELINE_SCHEMA = "bbe3c7d:business-service/src/schema.sql"


def main():
    assert BINARY.is_file(), "Build the business-service debug binary first"
    business_port, fixture_port = free_port(), free_port()
    while fixture_port == business_port:
        fixture_port = free_port()
    business_url = f"http://127.0.0.1:{business_port}"
    fixture_url = f"http://127.0.0.1:{fixture_port}"
    api_key = "reply-check-" + uuid.uuid4().hex
    account = "reply-check"
    external = "split-limit-" + uuid.uuid4().hex

    with tempfile.TemporaryDirectory(prefix="douyin-reply-split-") as folder:
        temp = Path(folder)
        db_path = temp / "business.sqlite"
        old_schema = subprocess.check_output(["git", "show", BASELINE_SCHEMA], cwd=ROOT,
                                             encoding="utf-8")
        with closing(sqlite3.connect(db_path)) as db:
            db.create_function("unixepoch", 0, lambda: int(time.time()))
            db.executescript(old_schema)
            db.execute("INSERT INTO conversations(id,account_id,external_id,customer_id) VALUES('old','old','old','old')")
            db.execute("INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status) VALUES('old-event','old','old-event','old-message','old','旧消息',1,'processed')")
            db.execute("INSERT INTO outbox(id,event_id,conversation_id,revision,text,status) VALUES('old-outbox','old-event','old',1,'旧回复','mock_sent')")
            db.commit()
        accounts = temp / "accounts.json"
        accounts.write_text(json.dumps([{"id": account, "merchant_id": "fixture-merchant",
            "industry": "教育教培", "fastgpt_app_id": "fixture-app",
            "fastgpt_key_env": "FASTGPT_APP_KEY"}]), encoding="utf-8")
        fixture_env = {**os.environ, "MOCK_PORT": str(fixture_port)}
        service_env = {**os.environ, "BUSINESS_API_KEY": api_key,
            "INTERNAL_API_KEY": "internal-" + uuid.uuid4().hex,
            "BIND": f"127.0.0.1:{business_port}", "DATABASE_PATH": str(db_path),
            "ACCOUNTS_FILE": str(accounts), "FASTGPT_URL": fixture_url,
            "FASTGPT_APP_KEY": "fixture-key", "SEND_MODE": "mock",
            "ADMIN_TOOLS_AUTO_LOGIN": "false"}
        with (temp / "fixture.log").open("w", encoding="utf-8") as fixture_log, \
             (temp / "business.log").open("w", encoding="utf-8") as business_log:
            fixture = subprocess.Popen([os.sys.executable, str(ROOT / "tools/fastgpt_fixture.py")],
                                       env=fixture_env, stdout=fixture_log, stderr=fixture_log)
            business = subprocess.Popen([str(BINARY)], env=service_env,
                                        stdout=business_log, stderr=business_log)
            try:
                wait_for(lambda: http(business_url + "/health"),
                         lambda body: body.get("status") == "ok", "business health")
                wait_for(lambda: http(fixture_url + "/calls"),
                         lambda body: "calls" in body, "fixture health")

                def snap():
                    return http(business_url + "/api/snapshot", key=api_key)

                def engine():
                    return http(fixture_url + "/calls")

                def send(index, text):
                    return http(business_url + "/webhooks/platform/" + account, {
                        "event_id": f"event-{index}", "message_id": f"message-{index}",
                        "conversation_id": external, "customer_id": "customer-1", "text": text,
                    }, api_key)

                with closing(sqlite3.connect(db_path)) as db:
                    assert db.execute("SELECT reply_rounds FROM conversations WHERE id='old'").fetchone() == (1,)
                    assert db.execute("SELECT text,part_index FROM outbox WHERE id='old-outbox'").fetchone() == ("旧回复", 0)

                first = send(1, "split-long-reply")
                state = wait_for(snap, lambda data: len([row for row in data["outbox"]
                    if row["event_id"] == first["id"] and row["status"] == "mock_sent"]) == 2,
                    "two sent parts from one generation")
                parts = sorted((row for row in state["outbox"] if row["event_id"] == first["id"]),
                               key=lambda row: row["part_index"])
                assert [row["part_index"] for row in parts] == [0, 1], parts
                assert len(engine()["calls"]) == 1, engine()["calls"]
                chat_id = engine()["calls"][0]["chatId"]
                assert "".join(row["text"] for row in parts) == engine()["histories"][chat_id][1]["content"]
                assert send(1, "split-long-reply")["duplicate"]
                assert len(engine()["calls"]) == 1

                for index in range(2, 51):
                    event = send(index, f"第{index}轮")
                    wait_for(snap, lambda data: any(row["event_id"] == event["id"]
                        and row["status"] == "mock_sent" for row in data["outbox"]),
                        f"reply round {index}")
                state = snap()
                conversation = next(row for row in state["conversations"] if row["external_id"] == external)
                assert conversation["reply_rounds"] == 50 and conversation["mode"] == "paused", conversation
                assert len(engine()["calls"]) == 50, len(engine()["calls"])
                held = send(51, "第51轮")
                assert held["status"] == "held", held
                time.sleep(0.4)
                assert len(engine()["calls"]) == 50
                try:
                    http(business_url + "/api/conversations/" + conversation["id"] + "/control",
                         {"mode": "bot"}, api_key)
                    raise AssertionError("Bot resumed after reply limit")
                except urllib.error.HTTPError as error:
                    assert error.code == 409, error.code
                print(json.dumps({"result": "passed", "checks": ["old outbox migration preserved",
                    "one FastGPT call produced two ordered sends", "duplicate event deduplicated",
                    "50 generated rounds stop the bot", "51st message held without model call",
                    "resume above limit rejected"]}))
            finally:
                business.terminate()
                fixture.terminate()
                business.wait(timeout=10)
                fixture.wait(timeout=10)


if __name__ == "__main__":
    main()
