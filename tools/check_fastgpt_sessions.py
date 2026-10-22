"""Focused FastGPT chatId contract check using an isolated HTTP fixture and SQLite."""

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
import uuid


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "business-service/target/debug/douyin-chatbot-service.exe"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(url, data=None, key=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["x-api-key"] = key
    request = urllib.request.Request(
        url,
        data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def wait_for(read, predicate, label, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = read()
            if predicate(value):
                return value
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise AssertionError("timed out: " + label)


def main():
    assert BINARY.is_file(), "build the business-service debug binary first"
    business_port = free_port()
    fixture_port = free_port()
    while fixture_port == business_port:
        fixture_port = free_port()
    business_url = f"http://127.0.0.1:{business_port}"
    fixture_url = f"http://127.0.0.1:{fixture_port}"
    api_key = "session-check-" + uuid.uuid4().hex
    account = "session-check"
    external = "dialogue-" + uuid.uuid4().hex
    checks = []

    with tempfile.TemporaryDirectory(prefix="douyin-fastgpt-session-") as folder:
        temp = Path(folder)
        db_path = temp / "business.sqlite"
        accounts = temp / "accounts.json"
        accounts.write_text(json.dumps([{
            "id": account, "merchant_id": "fixture-merchant", "industry": "装修装饰",
            "fastgpt_app_id": "fixture-app", "fastgpt_key_env": "FASTGPT_APP_KEY",
        }], ensure_ascii=False), encoding="utf-8")
        fixture_env = {**os.environ, "MOCK_PORT": fixture_url.rsplit(":", 1)[1]}
        service_env = {**os.environ, "BUSINESS_API_KEY": api_key,
                       "INTERNAL_API_KEY": "internal-" + uuid.uuid4().hex,
                       "BIND": business_url.removeprefix("http://"),
                       "DATABASE_PATH": str(db_path), "ACCOUNTS_FILE": str(accounts),
                       "FASTGPT_URL": fixture_url, "FASTGPT_APP_KEY": "fixture-key",
                       "SEND_MODE": "mock", "ADMIN_TOOLS_AUTO_LOGIN": "false"}
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

                def send(number, text):
                    return http(business_url + "/webhooks/platform/" + account, {
                        "event_id": f"event-{number}", "message_id": f"message-{number}",
                        "conversation_id": external, "customer_id": "customer-1", "text": text,
                    }, api_key)

                def sent(event):
                    return wait_for(snap, lambda data: any(
                        item["event_id"] == event["id"] and item["status"] == "mock_sent"
                        for item in data["outbox"]), "mock send " + event["id"])

                first = send("first", "老房翻新")
                sent(first)
                chat_id = engine()["calls"][0]["chatId"]
                assert engine()["calls"][0]["messages"] == [{"role": "user", "content": "老房翻新"}]
                count = len(engine()["calls"])
                assert send("first", "老房翻新")["duplicate"]
                assert len(engine()["calls"]) == count
                checks.append("single current message and duplicate callback do not replay FastGPT")

                for index in range(1, 31):
                    sent(send(f"long-{index}", f"第{index}轮需求"))
                state = engine()
                assert len(state["calls"]) == 31
                assert all(call["chatId"] == chat_id and len(call["messages"]) == 1
                           for call in state["calls"])
                assert len(state["histories"][chat_id]) == 62
                with sqlite3.connect(db_path) as db:
                    local_pairs = db.execute("SELECT e.text,o.text FROM events e JOIN outbox o ON o.event_id=e.id WHERE e.conversation_id=? AND o.status='mock_sent' ORDER BY e.rowid", (first["conversation_id"],)).fetchall()
                db.close()
                flattened = [entry for pair in local_pairs for entry in (
                    {"role": "user", "content": pair[0]},
                    {"role": "assistant", "content": pair[1]},
                )]
                assert flattened == state["histories"][chat_id]
                checks.append("30 follow-up turns retain one chatId; fixture and local sent history match")

                control_url = business_url + "/api/conversations/" + first["conversation_id"] + "/control"
                assert http(control_url, {"mode": "paused"}, api_key)["changed"]
                held = send("held", "暂停期间的消息")
                assert held["status"] == "held" and len(engine()["calls"]) == 31
                assert http(control_url, {"mode": "bot"}, api_key)["reset_session"]
                sent(send("resume", "恢复后的新问题"))
                resumed_id = engine()["calls"][-1]["chatId"]
                assert resumed_id != chat_id
                assert len(engine()["histories"][resumed_id]) == 2
                checks.append("pause holds messages and resume creates a clean FastGPT epoch")

                failed = send("failure", "fail-after-save")
                wait_for(snap, lambda data: any(item["id"] == failed["id"] and item["status"] == "uncertain"
                                               for item in data["events"]), "uncertain model call")
                count = len(engine()["calls"])
                assert send("failure", "fail-after-save")["duplicate"]
                time.sleep(0.5)
                assert len(engine()["calls"]) == count
                http(business_url + "/api/events/" + failed["id"] + "/cancel", {}, api_key)
                sent(send("after-failure", "核对后的问题"))
                after_failure_id = engine()["calls"][-1]["chatId"]
                assert after_failure_id != resumed_id
                checks.append("ambiguous FastGPT result is not retried; operator cancellation rotates epoch")

                slow = send("slow", "slow generated but stale")
                wait_for(engine, lambda data: data["calls"][-1]["text"] == "slow generated but stale",
                         "slow request entered fixture")
                newer = send("newer", "更新后的需求")
                wait_for(snap, lambda data: any(item["id"] == slow["id"] and item["status"] == "cancelled"
                                               for item in data["events"]), "stale response discarded", timeout=10)
                sent(newer)
                assert not any(item["event_id"] == slow["id"] for item in snap()["outbox"])
                assert engine()["calls"][-1]["chatId"] != after_failure_id
                checks.append("superseded generated answer is not sent or reused in active chatId")
            finally:
                business.terminate()
                fixture.terminate()
                business.wait(timeout=10)
                fixture.wait(timeout=10)

    result = {"date": "2026-10-20", "result": "passed", "checks": checks,
              "scope": "isolated business service and FastGPT HTTP fixture",
              "real_fastgpt_workflow_verified": False, "real_channel_verified": False,
              "full_tests_run": False}
    (ROOT / "artifacts/fastgpt-session-checks.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
