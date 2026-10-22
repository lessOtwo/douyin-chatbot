"""Focused live FastGPT check for phone/WeChat contact-channel decisions."""

import json
from pathlib import Path
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
settings = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#") and "=" in line)
key = settings.get("FASTGPT_APP_KEY", "")
if not key:
    raise RuntimeError("FASTGPT_APP_KEY is not configured")
profile = {"fields": {"所属行业": "教育教培", "完整门店名称": "示例门店"},
           "projects": {"main": [{"name": "语言培训", "question": "您是准备学口语还是备考？"}],
                        "available": [], "excluded": []}}
variables = {"merchant_id": "lead-method-check", "industry": "教育教培",
             "project_selection_question": "您好，您想咨询语言培训还是其他相关需求？",
             "merchant_profile": json.dumps(profile, ensure_ascii=False)}


def ask(chat_id, text, nickname=""):
    body = {"appId": "6ad7276957c1257e1bb5ed75", "chatId": chat_id,
            "stream": False, "messages": [{"role": "user", "content": text}],
            "variables": {**variables, "customer_nickname": nickname}}
    request = urllib.request.Request("http://127.0.0.1:3000/api/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    return json.loads(result["choices"][0]["message"]["content"])


def main():
    wechat = ask("lead-method-wechat-" + uuid.uuid4().hex,
                 "我的手机号是13800000000，不要打电话，请加我微信")
    assert wechat["fields"].get("phone") == "13800000000" and wechat["fields"].get("contact_method") == "wechat", wechat
    direct = ask("lead-method-phone-" + uuid.uuid4().hex,
                 "我的手机号是13900000000，请给我打电话")
    assert direct["fields"].get("phone") == "13900000000" and direct["fields"].get("contact_method") == "phone", direct
    chat_id = "lead-method-later-" + uuid.uuid4().hex
    first = ask(chat_id, "我的手机号是13700000000，请给我打电话")
    assert first["fields"].get("phone") == "13700000000" and first["fields"].get("contact_method") == "phone", first
    changed = ask(chat_id, "别打电话，加我微信")
    assert changed["fields"].get("contact_method") == "wechat" and changed["fields"].get("contact_value") == "13700000000", changed
    assert "phone" not in changed["fields"], changed
    peer_message = ask("peer-message-" + uuid.uuid4().hex,
                       "我们是做考研培训的机构，想合作招生，我的电话是13600000000")
    assert peer_message == {"reply": "不用呢 谢谢", "action": "peer", "fields": {}}, peer_message
    peer_nickname = ask("peer-nickname-" + uuid.uuid4().hex,
                        "你好，我们可以合作招生吗？", "泰安成人学历招生中心")
    assert peer_nickname == {"reply": "不用呢 谢谢", "action": "peer", "fields": {}}, peer_nickname
    consumer = ask("peer-consumer-" + uuid.uuid4().hex,
                   "我是想报成人本科的，可以咨询吗？", "泰安学生家长")
    assert consumer["action"] != "peer", consumer
    print(json.dumps({"result": "passed", "checks": ["phone-based WeChat",
        "direct phone call", "later contact-channel correction",
        "peer from message", "peer from nickname", "consumer is not peer"],
        "later_reply": changed["reply"]}))


if __name__ == "__main__":
    main()
