"""Targeted read of the published workflow's response with a fresh FastGPT chatId."""

import json
from pathlib import Path
import urllib.request
import uuid


root = Path(__file__).resolve().parents[1]
settings = dict(line.split("=", 1) for line in (root / ".env").read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#") and "=" in line)
key = settings.get("FASTGPT_APP_KEY", "")
if not key:
    raise RuntimeError("FASTGPT_APP_KEY is not configured")
profile = {
    "fields": {"所属行业": "教育教培", "完整门店名称": "示例门店", "资料或服务": "课程咨询"},
    "projects": {
        "main": [{"name": "语言培训", "question": "语言培训问句"}],
        "available": [{"name": "成人学历提升", "question": "成人学历提升问句"}],
        "excluded": [],
    },
}
question = "您好，您想咨询语言培训、成人学历提升还是其他相关需求？"
body = {
    "appId": "6ad7276957c1257e1bb5ed75", "chatId": "selection-check-" + uuid.uuid4().hex,
    "stream": False, "messages": [{"role": "user", "content": "你好"}],
    "variables": {"merchant_id": "selection-check", "industry": "教育教培",
                  "project_selection_question": question,
                  "merchant_profile": json.dumps(profile, ensure_ascii=False)},
}
request = urllib.request.Request("http://127.0.0.1:3000/api/v1/chat/completions",
                             data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                             headers={"Authorization": "Bearer " + key,
                                      "Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=120) as response:
    result = json.load(response)
decision = json.loads(result["choices"][0]["message"]["content"])
reply = decision["reply"]
if "语言培训" not in reply or "成人学历提升" not in reply:
    raise AssertionError("Published workflow omitted a configured project: " + reply)
print(json.dumps({"result": "passed", "reply": reply}))
