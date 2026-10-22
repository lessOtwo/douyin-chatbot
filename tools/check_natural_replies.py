"""Focused live conversation check for concise replies and existing lead branches."""

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

profile = {
    "fields": {
        "所属行业": "教育教培",
        "完整门店名称": "测试教育门店",
        "服务区域": "泰安市（含肥城市）",
        "门店联系电话": "0538-1234567",
        "门店电话可公开": "是",
        "资料或服务": "提供学习资料和课程咨询",
    },
    "projects": {
        "main": [{"name": "语言培训", "question": "您是想学日常口语还是考证？"}],
        "available": [{"name": "成人学历提升", "question": "您想提升到专科还是本科？"}],
        "excluded": [],
    },
}
variables = {
    "merchant_id": "natural-reply-check",
    "industry": "教育教培",
    "project_selection_question": "您好，您想咨询语言培训、成人学历提升还是其他相关需求？",
    "merchant_profile": json.dumps(profile, ensure_ascii=False),
    "customer_nickname": "",
}


def ask(chat_id, message):
    payload = {
        "appId": "6ad7276957c1257e1bb5ed75",
        "chatId": chat_id,
        "stream": False,
        "messages": [{"role": "user", "content": message}],
        "variables": variables,
    }
    request = urllib.request.Request(
        "http://127.0.0.1:3000/api/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    return json.loads(result["choices"][0]["message"]["content"])


def main():
    chat_id = "natural-reply-" + uuid.uuid4().hex
    messages = [
        "你好",
        "我想问下你们这成人本科培训班的价格",
        "你们的电话有吗？",
        "我是泰安肥城的，这里能提供服务吗？",
        "我的电话是13800000000",
        "别打电话，用微信加我",
    ]
    decisions = [(message, ask(chat_id, message)) for message in messages]
    print(json.dumps({"conversation": decisions}, ensure_ascii=True, indent=2))
    opening, price, store_phone, coverage, lead, preference = [item[1] for item in decisions]
    assert "语言培训" in opening["reply"] and "成人学历提升" in opening["reply"], opening
    assert price["action"] == "reply" and len(price["reply"]) <= 90, price
    assert "电话" in price["reply"] or "微信" in price["reply"], price
    assert "0538-1234567" in store_phone["reply"] and len(store_phone["reply"]) <= 50, store_phone
    assert "留" not in store_phone["reply"], store_phone
    assert "肥城" in coverage["reply"] and len(coverage["reply"]) <= 70, coverage
    assert "电话" not in coverage["reply"] and "微信" not in coverage["reply"], coverage
    assert lead["reply"] == "收到，稍后店长联系您详聊。", lead
    assert lead["fields"].get("phone") == "13800000000", lead
    assert preference["fields"].get("contact_method") == "wechat", preference
    assert preference["fields"].get("contact_value") == "13800000000", preference
    assert len(preference["reply"]) <= 50, preference
    print(json.dumps({"result": "passed"}))


if __name__ == "__main__":
    main()
