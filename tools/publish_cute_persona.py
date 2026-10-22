"""Publish the focused客服小妹 prompt change with guarded rollback."""

import argparse
import copy
import hashlib
import http.cookiejar
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request

from build_fastgpt_workflow import env_password


ROOT = Path(__file__).resolve().parents[1]
APP_ID = "6ad7276957c1257e1bb5ed75"
BASELINE_COMMIT = "bbe3c7d"
BACKUP = ROOT / "artifacts/fastgpt-cute-persona-backup.json"
AI_NODES = ("lead_received_reply", "contact_reply", "retry_reply", "normal_reply")


def prompt(node):
    return next(item for item in node["inputs"] if item["key"] == "systemPrompt")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def request(path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        with opener.open(urllib.request.Request(args.base_url.rstrip("/") + path, data=data,
                         headers={"Content-Type": "application/json", "fastgpt-web-request": "1"}),
                         timeout=60) as response:
            result = json.load(response)
        if result.get("code") != 200:
            raise RuntimeError(f"FastGPT rejected {path}: {result.get('msg')}")
        return result.get("data")

    pre = request("/api/support/user/account/preLogin?username=root")
    request("/api/support/user/account/loginByPassword", {
        "username": "root", "password": hashlib.sha256(env_password().encode("utf-8")).hexdigest(),
        "code": pre["code"], "language": "zh-CN",
    })
    query = urllib.parse.urlencode({"appId": APP_ID})
    app = request("/api/core/app/detail?" + query)
    old_id = app.get("publishedVersionId")
    if not old_id:
        raise RuntimeError("Workflow has no published version")
    published = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": old_id,
    }))
    for key in ("nodes", "edges", "chatConfig"):
        if app[key] != published[key]:
            raise RuntimeError(f"Unpublished {key} changes exist; refusing to overwrite")

    if args.restore:
        backup = json.loads(BACKUP.read_text(encoding="utf-8"))
        if old_id != backup["new_version_id"]:
            raise RuntimeError("Published version changed since this experiment; refusing rollback")
        target = backup["previous_workflow"]
        version_name = "恢复客服语气实验前版本"
    else:
        old_source = subprocess.check_output(["git", "show",
            f"{BASELINE_COMMIT}:config/fastgpt-system-prompt.md"], cwd=ROOT, encoding="utf-8")
        new_source = (ROOT / "config/fastgpt-system-prompt.md").read_text(encoding="utf-8")
        old_parts, new_parts = old_source.split("\n\n"), new_source.split("\n\n")
        if len(old_parts) != len(new_parts):
            raise RuntimeError("System prompt paragraph structure changed")
        replacements = [(old, new) for old, new in zip(old_parts, new_parts) if old != new]
        if len(replacements) != 2:
            raise RuntimeError(f"Expected two persona prompt changes, got {len(replacements)}")
        target = copy.deepcopy(published)
        nodes = {node["nodeId"]: node for node in target["nodes"]}
        for node_id in AI_NODES:
            value = prompt(nodes[node_id])["value"]
            for old, new in replacements:
                if value.count(old) != 1:
                    raise RuntimeError(f"Published {node_id} prompt differs from expected wording")
                value = value.replace(old, new, 1)
            prompt(nodes[node_id])["value"] = value
        version_name = "客服小妹语气"

    print(f"Ready: {old_id} -> {version_name}; {len(target['nodes'])} nodes")
    if not args.apply and not args.restore:
        return
    if args.apply:
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        BACKUP.write_text(json.dumps({"previous_version_id": old_id, "new_version_id": None,
            "previous_workflow": published}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    request("/api/core/app/version/publish?" + query, {
        "nodes": target["nodes"], "edges": target["edges"],
        "chatConfig": target["chatConfig"], "isPublish": True,
        "versionName": version_name,
    })
    new_id = request("/api/core/app/detail?" + query).get("publishedVersionId")
    if not new_id or new_id == old_id:
        raise RuntimeError("Published version did not change")
    actual = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": new_id,
    }))
    for key in ("nodes", "edges", "chatConfig"):
        if actual[key] != target[key]:
            raise RuntimeError(f"Published {key} differs from requested workflow")
    if args.apply:
        BACKUP.write_text(json.dumps({"previous_version_id": old_id, "new_version_id": new_id,
            "previous_workflow": published}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified published version: {new_id}")


if __name__ == "__main__":
    main()
