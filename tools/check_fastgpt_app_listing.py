"""Read-only check of workflow listing and publication state on the local FastGPT."""
import hashlib
import http.cookiejar
import json
import urllib.parse
import urllib.request

from build_fastgpt_workflow import env_password


BASE = "http://127.0.0.1:3000"
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)


def request(path, body=None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    with opener.open(urllib.request.Request(
        BASE + path,
        data=payload,
        headers={"Content-Type": "application/json", "fastgpt-web-request": "1"},
    ), timeout=30) as response:
        result = json.load(response)
    if result.get("code") != 200:
        raise RuntimeError(f"FastGPT request failed: {path}: {result.get('msg')}")
    return result.get("data")


pre = request("/api/support/user/account/preLogin?username=root")
request("/api/support/user/account/loginByPassword", {
    "username": "root",
    "password": hashlib.sha256(env_password().encode("utf-8")).hexdigest(),
    "code": pre["code"],
    "language": "zh-CN",
})
page = request("/api/core/app/listV2", {"type": "advanced", "pageNum": 1, "pageSize": 50})
items = []
for app in page["list"]:
    detail = request("/api/core/app/detail?" + urllib.parse.urlencode({"appId": app["_id"]}))
    versions = request("/api/core/app/version/list", {"appId": app["_id"], "pageNum": 1, "pageSize": 20})
    items.append({"name": app["name"], "id": app["_id"],
                  "has_published_version": bool(detail.get("publishedVersionId")),
                  "versions": [{"is_publish": version.get("isPublish"), "name": version.get("versionName")}
                               for version in versions["list"]]})
print(json.dumps({"total_root_workflows": page["total"], "items": items}, ensure_ascii=False))
