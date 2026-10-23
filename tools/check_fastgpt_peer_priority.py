"""Check that a business nickname wins over a lead-like message."""

import argparse
import json
from pathlib import Path
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
settings = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#") and "=" in line)
KEY = settings.get("FASTGPT_APP_KEY", "")
if not KEY:
    raise RuntimeError("FASTGPT_APP_KEY is not configured")
PROFILE = {"fields": {"所属行业": "家政服务", "完整门店名称": "示例家政门店"},
           "projects": {"main": [{"name": "母婴护理", "question": "想了解产后照护还是宝宝照护？"}],
                        "available": [], "excluded": []}}
VARIABLES = {"merchant_id": "peer-priority-check", "industry": "家政服务",
             "project_selection_question": "你好呀～想咨询母婴护理，还是其他相关需求呢？",
             "merchant_profile": json.dumps(PROFILE, ensure_ascii=False)}


def ask(chat_id, nickname, text):
    body = {"appId": "6ad7276957c1257e1bb5ed75", "chatId": chat_id,
            "stream": False, "messages": [{"role": "user", "content": text}],
            "variables": {**VARIABLES, "customer_nickname": nickname}}
    request = urllib.request.Request("http://127.0.0.1:3000/api/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    return json.loads(result["choices"][0]["message"]["content"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    peer_chat = "peer-priority-" + uuid.uuid4().hex
    peer_greeting = ask(peer_chat, "泰安月嫂找红姐", "hi")
    peer_contact = ask(peer_chat, "泰安月嫂找红姐", "需要日间夜间月嫂联系我，13800000000")
    consumer = ask("peer-consumer-" + uuid.uuid4().hex, "想找月嫂的张女士",
                   "需要日间夜间月嫂联系我，13900000000")
    result = {"peer_greeting": peer_greeting["action"],
              "peer_contact": peer_contact["action"],
              "peer_contact_reply": peer_contact["reply"],
              "consumer": consumer["action"]}
    print(json.dumps(result, ensure_ascii=True))
    if args.verify:
        assert peer_greeting == {"reply": "不用呢 谢谢", "action": "peer", "fields": {}}, result
        assert peer_contact == {"reply": "不用呢 谢谢", "action": "peer", "fields": {}}, result
        assert consumer["action"] != "peer", result


if __name__ == "__main__":
    main()
