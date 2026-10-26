"""Update staff titles in the published local FastGPT workflow; dry-run by default."""

import argparse
import hashlib
import http.cookiejar
import json
from pathlib import Path
import urllib.parse
import urllib.request

from build_fastgpt_workflow import env_password


ROOT = Path(__file__).resolve().parents[1]
APP_ID = "6ad7276957c1257e1bb5ed75"
ACCOUNT_ID = "1324657980"
OLD_CORE = "账号已展示商家身份，留资时说“店里老师”即可，不必再报门店全称；客户追问主体时再按资料回答。"
NEW_CORE = ("账号已展示商家身份，留资时按当前商家行业选择合适的岗位称呼，不必再报门店全称："
            "教育教培可称“老师”；家政服务仅在岗位明确时，家电家居维修可称“师傅”，"
            "日常保洁、母婴护理或养老陪护可称“阿姨”；钟点工、宠物托管、多个项目并列或岗位不明时，"
            "以及其他行业无法确认称呼时，一律称“店里工作人员”。绝不能把其他行业的商家人员称为“老师”。"
            "客户追问主体时再按资料回答。")
OLD_NORMAL = "说清由店里老师答复即可"
NEW_NORMAL = ("称呼必须依当前商家行业：教育教培可称老师；家政服务维修岗位可称师傅，"
              "保洁、母婴护理、养老陪护岗位可称阿姨；钟点工、宠物托管、多项目或岗位不明及其他行业，"
              "一律说店里工作人员会答复。不要对家政等非教培行业说店里老师")
REPLACEMENTS = [
    (OLD_CORE, NEW_CORE),
    ("说清是为了让店里老师答复", "说清是为了让店里工作人员答复（岗位明确时可用上述称呼）"),
    ("让店里老师说明", "让店里工作人员说明（岗位明确时可用上述称呼）"),
    (OLD_NORMAL, NEW_NORMAL),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                    if line and not line.startswith("#") and "=" in line)
    request = urllib.request.Request("http://127.0.0.1:8088/api/accounts",
                                     headers={"x-api-key": settings["BUSINESS_API_KEY"]})
    with urllib.request.urlopen(request, timeout=15) as response:
        accounts = json.load(response)["accounts"]
    account = next((item for item in accounts if item["id"] == ACCOUNT_ID), None)
    if not account or account["fastgpt_app_id"] != APP_ID:
        raise RuntimeError("Target Douyin account is not bound to the expected workflow")

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def api(path, body=None):
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(args.base_url.rstrip("/") + path, data=payload,
                                         headers={"Content-Type": "application/json", "fastgpt-web-request": "1"})
        with opener.open(request, timeout=45) as response:
            result = json.load(response)
        if result.get("code") != 200:
            raise RuntimeError(f"FastGPT rejected {path}: {result.get('msg')}")
        return result.get("data")

    pre = api("/api/support/user/account/preLogin?username=root")
    api("/api/support/user/account/loginByPassword", {
        "username": "root", "password": hashlib.sha256(env_password().encode("utf-8")).hexdigest(),
        "code": pre["code"], "language": "zh-CN",
    })
    app_query = urllib.parse.urlencode({"appId": APP_ID})
    app = api("/api/core/app/detail?" + app_query)
    previous = app.get("publishedVersionId")
    if not previous:
        raise RuntimeError("Target workflow is not published")
    version = api("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": previous,
    }))
    changes = 0
    for node in version["nodes"]:
        for item in node.get("inputs", []):
            if item.get("key") != "systemPrompt" or not isinstance(item.get("value"), str):
                continue
            original = item["value"]
            updated = original
            for old, new in REPLACEMENTS:
                updated = updated.replace(old, new)
            if updated != original:
                item["value"] = updated
                changes += 1
    prompts = [item["value"] for node in version["nodes"] for item in node.get("inputs", [])
               if item.get("key") == "systemPrompt" and isinstance(item.get("value"), str)]
    if changes == 0:
        if any(NEW_CORE in prompt for prompt in prompts) and any(NEW_NORMAL in prompt for prompt in prompts):
            print("Published workflow already has industry-specific staff titles")
            return
        raise RuntimeError("Published prompts did not contain the expected staff-title text")
    if any(OLD_CORE in prompt or OLD_NORMAL in prompt for prompt in prompts):
        raise RuntimeError("Some old staff-title rules remain")
    print(f"Ready to update {changes} prompt fields in published workflow {previous}")
    if not args.apply:
        return
    api("/api/core/app/version/publish?" + app_query, {
        "nodes": version["nodes"], "edges": version["edges"],
        "chatConfig": version["chatConfig"], "isPublish": True,
        "versionName": "按行业使用人员称呼",
    })
    updated = api("/api/core/app/detail?" + app_query)
    current = updated.get("publishedVersionId")
    if not current or current == previous:
        raise RuntimeError("Published workflow version did not change")
    readback = api("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": current,
    }))
    prompts = [item["value"] for node in readback["nodes"] for item in node.get("inputs", [])
               if item.get("key") == "systemPrompt" and isinstance(item.get("value"), str)]
    if not any(NEW_CORE in prompt for prompt in prompts) or not any(NEW_NORMAL in prompt for prompt in prompts):
        raise RuntimeError("Published prompt readback does not contain the new staff-title rules")
    print(f"Published and verified workflow version {current}; changed prompts: {changes}")


if __name__ == "__main__":
    main()
