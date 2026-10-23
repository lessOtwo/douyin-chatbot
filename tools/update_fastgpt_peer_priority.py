"""Update only peer-classification wording in the published local FastGPT workflow."""

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
OLD = json.loads(subprocess.check_output(
    ["git", "show", "8445e00:config/fastgpt-lead-workflow.json"], cwd=ROOT))
NEW = json.loads((ROOT / "config/fastgpt-lead-workflow.json").read_text(encoding="utf-8"))


def get_input(node, key):
    return next(value for value in node["inputs"] if value["key"] == key)


def changed_inputs():
    old_nodes = {node["nodeId"]: node for node in OLD["nodes"]}
    new_nodes = {node["nodeId"]: node for node in NEW["nodes"]}
    if old_nodes.keys() != new_nodes.keys() or OLD["edges"] != NEW["edges"] or OLD["chatConfig"] != NEW["chatConfig"]:
        raise RuntimeError("Source workflow structure changed; review before publishing")
    changes = []
    for node_id, keys in [("lead_intent", ("systemPrompt", "agents"))]:
        for key in keys:
            before = get_input(old_nodes[node_id], key)["value"]
            after = get_input(new_nodes[node_id], key)["value"]
            if before != after:
                changes.append((node_id, key, before, after))
    if not changes:
        raise RuntimeError("No peer-classification changes found in source workflow")
    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(args.base_url.rstrip("/") + path, data=data,
                                     headers={"Content-Type": "application/json", "fastgpt-web-request": "1"})
        with opener.open(req, timeout=45) as response:
            result = json.load(response)
        if result.get("code") != 200:
            raise RuntimeError(f"FastGPT rejected {path}: {result.get('msg')}")
        return result.get("data")

    pre = request("/api/support/user/account/preLogin?username=root")
    request("/api/support/user/account/loginByPassword", {
        "username": "root", "password": hashlib.sha256(env_password().encode("utf-8")).hexdigest(),
        "code": pre["code"], "language": "zh-CN"})
    query = urllib.parse.urlencode({"appId": APP_ID})
    app = request("/api/core/app/detail?" + query)
    old_version = app.get("publishedVersionId")
    if not old_version:
        raise RuntimeError("Workflow has no published version")
    published = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": old_version}))
    if app["nodes"] != published["nodes"] or app["edges"] != published["edges"] or app["chatConfig"] != published["chatConfig"]:
        raise RuntimeError("Unpublished draft differs from published workflow; refusing to replace it")

    changes = changed_inputs()
    patched = copy.deepcopy(published)
    nodes = {node["nodeId"]: node for node in patched["nodes"]}
    for node_id, key, before, after in changes:
        current = get_input(nodes[node_id], key)["value"]
        if current not in (before, after):
            raise RuntimeError(f"Published {node_id}.{key} differs from local baseline; review it")
        get_input(nodes[node_id], key)["value"] = after
    if patched["nodes"] == published["nodes"]:
        print("Peer priority is already published")
        return
    print(f"Ready to update {len(changes)} prompt inputs in published version {old_version}")
    if not args.apply:
        return

    payload = {"nodes": patched["nodes"], "edges": patched["edges"],
               "chatConfig": patched["chatConfig"]}
    request("/api/core/app/version/publish?" + query, {
        **payload, "isPublish": False, "autoSave": True})
    draft = request("/api/core/app/detail?" + query)
    if draft["nodes"] != patched["nodes"] or draft["edges"] != patched["edges"] or draft["chatConfig"] != patched["chatConfig"]:
        raise RuntimeError("FastGPT did not retain the updated draft")
    request("/api/core/app/version/publish?" + query, {
        **payload, "isPublish": True, "versionName": "同行昵称优先识别"})
    app = request("/api/core/app/detail?" + query)
    new_version = app.get("publishedVersionId")
    if not new_version or new_version == old_version:
        raise RuntimeError("Published version did not change")
    current = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": new_version}))
    current_nodes = {node["nodeId"]: node for node in current["nodes"]}
    if any(get_input(current_nodes[node_id], key)["value"] != after for node_id, key, _, after in changes):
        raise RuntimeError("Published workflow did not retain peer-classification wording")
    print(f"Verified published version: {new_version}")


if __name__ == "__main__":
    main()
