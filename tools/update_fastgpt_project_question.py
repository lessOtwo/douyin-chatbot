"""Update only the project-selection wording in the currently published local workflow."""

import argparse
import hashlib
import http.cookiejar
import json
import urllib.parse
import urllib.request

from build_fastgpt_workflow import env_password


OLD = "使用传入的“项目选择问句”引导，最多列举三个主做项目，其余用“其他需求”概括，不拼接多个项目问句"
NEW = "直接使用传入的“项目选择问句”引导；该问句已经按主做项目在前、其他可做项目在后列出全部可做项目，不要自行删改、重新排序或拼接多个项目问句"
APP_ID = "6ad7276957c1257e1bb5ed75"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def request(path, body=None):
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        with opener.open(urllib.request.Request(
            args.base_url.rstrip("/") + path,
            data=payload,
            headers={"Content-Type": "application/json", "fastgpt-web-request": "1"},
        ), timeout=45) as response:
            result = json.load(response)
        if result.get("code") != 200:
            raise RuntimeError(f"FastGPT rejected {path}: {result.get('msg')}")
        return result.get("data")

    pre = request("/api/support/user/account/preLogin?username=root")
    request("/api/support/user/account/loginByPassword", {
        "username": "root",
        "password": hashlib.sha256(env_password().encode("utf-8")).hexdigest(),
        "code": pre["code"],
        "language": "zh-CN",
    })
    app_query = urllib.parse.urlencode({"appId": APP_ID})
    app = request("/api/core/app/detail?" + app_query)
    published_id = app.get("publishedVersionId")
    if not published_id:
        raise RuntimeError("Target workflow has no published version")
    version = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": published_id,
    }))
    count = 0
    for node in version["nodes"]:
        for item in node.get("inputs", []):
            if item.get("key") == "systemPrompt" and OLD in item.get("value", ""):
                item["value"] = item["value"].replace(OLD, NEW)
                count += 1
    if count == 0:
        if all(NEW in str(node) or OLD not in str(node) for node in version["nodes"]):
            print("Published workflow already uses the updated project question")
            return
        raise RuntimeError("Published workflow prompt did not match expected wording")
    print(f"Found {count} prompt fields to update in published version {published_id}")
    if not args.apply:
        return
    request("/api/core/app/version/publish?" + app_query, {
        "nodes": version["nodes"], "edges": version["edges"],
        "chatConfig": version["chatConfig"], "isPublish": True,
        "versionName": "主做及可做项目开场问句",
    })
    updated_app = request("/api/core/app/detail?" + app_query)
    updated_id = updated_app.get("publishedVersionId")
    if not updated_id or updated_id == published_id:
        raise RuntimeError("Published version did not change")
    updated = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": updated_id,
    }))
    updated_prompts = [item.get("value", "") for node in updated["nodes"]
                       for item in node.get("inputs", []) if item.get("key") == "systemPrompt"]
    if sum(NEW in prompt for prompt in updated_prompts) != count or any(OLD in prompt for prompt in updated_prompts):
        raise RuntimeError("Published workflow readback did not match the updated wording")
    print(f"Published workflow verified: {updated_id}, updated prompts: {count}")


if __name__ == "__main__":
    main()
