"""Focused live-model check for industry-specific staff titles."""

import json
from pathlib import Path
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
settings = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#") and "=" in line)


def ask(chat_id, message, industry, profile):
    variables = {
        "merchant_id": "title-check",
        "industry": industry,
        "project_selection_question": "你好呀～想咨询日常保洁，还是其他相关需求呢？",
        "merchant_profile": json.dumps(profile, ensure_ascii=False),
        "customer_nickname": "测试客户",
    }
    payload = {
        "appId": "6ad7276957c1257e1bb5ed75", "chatId": chat_id, "stream": False,
        "messages": [{"role": "user", "content": message}], "variables": variables,
    }
    request = urllib.request.Request(
        "http://127.0.0.1:3000/api/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + settings["FASTGPT_APP_KEY"],
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    return json.loads(result["choices"][0]["message"]["content"])


def main():
    profile = {"fields": {"所属行业": "家政服务", "完整门店名称": "测试家政门店",
                          "服务区域": "泰安市", "资料或服务": "提供保洁服务并预约门店沟通"},
               "projects": {"main": [{"name": "日常保洁", "question": "您是需要日常清洁还是深度清洁？"}],
                            "available": [], "excluded": []}}
    chat_id = "staff-title-" + uuid.uuid4().hex
    first = ask(chat_id, "我想做厨房深度保洁，包括油烟机和灶台精洗", "家政服务", profile)
    price = ask(chat_id, "多少钱？不能直接报价的话，让负责的人回复我", "家政服务", profile)
    assert first["action"] == "reply" and price["action"] == "reply", (first, price)
    assert "老师" not in first["reply"] and "老师" not in price["reply"], (first, price)
    assert any(word in price["reply"] for word in ("工作人员", "阿姨", "师傅")), price
    print(json.dumps({"result": "passed", "housekeeping": [first["reply"], price["reply"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
