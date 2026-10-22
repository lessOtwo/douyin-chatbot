"""Add a peer branch to the existing local FastGPT workflow without replacing other nodes."""

import argparse
import copy
import hashlib
import http.cookiejar
import json
from pathlib import Path
import urllib.parse
import urllib.request

from build_fastgpt_workflow import env_password


ROOT = Path(__file__).resolve().parents[1]
APP_ID = "6ad7276957c1257e1bb5ed75"
FLOW = json.loads((ROOT / "config/fastgpt-lead-workflow.json").read_text(encoding="utf-8"))
TARGET = {node["nodeId"]: node for node in FLOW["nodes"]}


def item(node, key):
    return next(value for value in node["inputs"] if value["key"] == key)


def add_peer(flow):
    nodes = {node["nodeId"]: node for node in flow["nodes"]}
    if "peer_reply" in nodes:
        raise RuntimeError("peer branch already exists")
    classifier = nodes["lead_intent"]
    agents = item(classifier, "agents")["value"]
    if any(agent["key"] == "peer" for agent in agents):
        raise RuntimeError("peer classifier already exists")
    item(classifier, "systemPrompt")["value"] = item(TARGET["lead_intent"], "systemPrompt")["value"]
    agents.insert(0, copy.deepcopy(item(TARGET["lead_intent"], "agents")["value"][0]))
    flow["nodes"].append(copy.deepcopy(TARGET["peer_reply"]))
    flow["edges"].append(copy.deepcopy(next(edge for edge in FLOW["edges"] if edge["target"] == "peer_reply")))
    variables = flow["chatConfig"]["variables"]
    if any(value["key"] == "customer_nickname" for value in variables):
        raise RuntimeError("nickname variable already exists")
    variables.append(copy.deepcopy(next(value for value in FLOW["chatConfig"]["variables"]
                                    if value["key"] == "customer_nickname")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        with opener.open(urllib.request.Request(args.base_url.rstrip("/") + path, data=data,
                         headers={"Content-Type": "application/json", "fastgpt-web-request": "1"}), timeout=45) as response:
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
    old_id = app.get("publishedVersionId")
    if not old_id:
        raise RuntimeError("workflow has no published version")
    published = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": old_id}))
    original = copy.deepcopy(published)
    add_peer(published)
    target_classifier = next(node for node in published["nodes"] if node["nodeId"] == "lead_intent")
    draft_has_peer = any(node["nodeId"] == "peer_reply" for node in app["nodes"])
    if draft_has_peer:
        if app["edges"] != published["edges"] or app["chatConfig"] != published["chatConfig"]:
            raise RuntimeError("unpublished draft differs in edges or variables")
        if {node["nodeId"] for node in app["nodes"]} != {node["nodeId"] for node in published["nodes"]}:
            raise RuntimeError("unpublished draft has different nodes")
        existing_classifier = next(node for node in app["nodes"] if node["nodeId"] == "lead_intent")
        for key in ("systemPrompt", "agents"):
            if item(existing_classifier, key)["value"] != item(target_classifier, key)["value"]:
                raise RuntimeError(f"unpublished classifier differs in {key}")
    else:
        for key in ("nodes", "edges", "chatConfig"):
            if app[key] != original[key]:
                raise RuntimeError(f"unpublished draft differs in {key}; refusing to overwrite")
    print(f"Ready: {len(published['nodes'])} nodes, {len(published['edges'])} edges, "
          f"{len(published['chatConfig']['variables'])} variables")
    if not args.apply:
        return
    payload = {"nodes": published["nodes"], "edges": published["edges"],
               "chatConfig": published["chatConfig"]}
    if not draft_has_peer:
        request("/api/core/app/version/publish?" + query, {
            **payload, "isPublish": False, "autoSave": True})
    draft = request("/api/core/app/detail?" + query)
    if {node["nodeId"] for node in draft["nodes"]} != {node["nodeId"] for node in published["nodes"]}:
        raise RuntimeError("draft did not retain nodes")
    if draft["edges"] != published["edges"] or draft["chatConfig"] != published["chatConfig"]:
        raise RuntimeError("draft did not retain edges or variables")
    draft_classifier = next(node for node in draft["nodes"] if node["nodeId"] == "lead_intent")
    target_classifier = next(node for node in published["nodes"] if node["nodeId"] == "lead_intent")
    for key in ("systemPrompt", "agents"):
        if item(draft_classifier, key)["value"] != item(target_classifier, key)["value"]:
            raise RuntimeError(f"draft did not retain classifier {key}")
    draft_peer = next(node for node in draft["nodes"] if node["nodeId"] == "peer_reply")
    if item(draft_peer, "text")["value"] != item(TARGET["peer_reply"], "text")["value"]:
        raise RuntimeError("draft did not retain peer reply")
    request("/api/core/app/version/publish?" + query, {
        **payload, "isPublish": True, "versionName": "同行识别与联系渠道"})
    app = request("/api/core/app/detail?" + query)
    new_id = app.get("publishedVersionId")
    if not new_id or new_id == old_id:
        raise RuntimeError("published version did not change")
    current = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": new_id}))
    if {node["nodeId"] for node in current["nodes"]} != {node["nodeId"] for node in published["nodes"]}:
        raise RuntimeError("published version did not retain peer node")
    if current["edges"] != published["edges"] or current["chatConfig"] != published["chatConfig"]:
        raise RuntimeError("published version did not retain edges or variables")
    classifier = next(node for node in current["nodes"] if node["nodeId"] == "lead_intent")
    for key in ("systemPrompt", "agents"):
        if item(classifier, key)["value"] != item(target_classifier, key)["value"]:
            raise RuntimeError(f"published classifier differs in {key}")
    print(f"Verified published version: {new_id}")


if __name__ == "__main__":
    main()
