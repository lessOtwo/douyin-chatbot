"""Publish the contact and peer policy onto the existing FastGPT workflow."""

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
BASELINE = "a7f024b"
OLD = json.loads(subprocess.check_output(
    ["git", "show", f"{BASELINE}:config/fastgpt-lead-workflow.json"], cwd=ROOT))
PREVIOUS = json.loads(subprocess.check_output(
    ["git", "show", f"{BASELINE}:config/fastgpt-lead-workflow.json"], cwd=ROOT))
NEW = json.loads((ROOT / "config/fastgpt-lead-workflow.json").read_text(encoding="utf-8"))
BACKUP = ROOT / "artifacts/fastgpt-contact-policy-backup.json"


def node_input(node, key):
    return next(item for item in node["inputs"] if item["key"] == key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        with opener.open(urllib.request.Request(
            args.base_url.rstrip("/") + path, data=data,
            headers={"Content-Type": "application/json", "fastgpt-web-request": "1"}),
            timeout=60) as response:
            result = json.load(response)
        if result.get("code") != 200:
            raise RuntimeError(f"FastGPT rejected {path}: {result.get('msg')}")
        return result.get("data")

    pre = request("/api/support/user/account/preLogin?username=root")
    request("/api/support/user/account/loginByPassword", {
        "username": "root", "password": hashlib.sha256(env_password().encode()).hexdigest(),
        "code": pre["code"], "language": "zh-CN"})
    query = urllib.parse.urlencode({"appId": APP_ID})
    app = request("/api/core/app/detail?" + query)
    version = app.get("publishedVersionId")
    if not version:
        raise RuntimeError("Workflow has no published version")
    published = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": version}))
    for key in ("nodes", "edges", "chatConfig"):
        if app[key] != published[key]:
            raise RuntimeError(f"Unpublished {key} changes exist; refusing to overwrite")
    if OLD["edges"] != NEW["edges"] or set(node["nodeId"] for node in OLD["nodes"]) != set(node["nodeId"] for node in NEW["nodes"]):
        raise RuntimeError("Source workflow topology changed")
    target = copy.deepcopy(published)
    old_nodes = {node["nodeId"]: node for node in OLD["nodes"]}
    previous_nodes = {node["nodeId"]: node for node in PREVIOUS["nodes"]}
    new_nodes = {node["nodeId"]: node for node in NEW["nodes"]}
    live_nodes = {node["nodeId"]: node for node in target["nodes"]}
    before_paragraphs = subprocess.check_output(["git", "show",
        f"{BASELINE}:config/fastgpt-system-prompt.md"], cwd=ROOT).decode().split("\n\n")
    after_paragraphs = (ROOT / "config/fastgpt-system-prompt.md").read_text(encoding="utf-8").split("\n\n")
    if len(before_paragraphs) != len(after_paragraphs):
        raise RuntimeError("System prompt paragraph layout changed")
    edge_signature = lambda edges: sorted((edge.get("source"), edge.get("target"), edge.get("sourceHandle")) for edge in edges)
    if set(live_nodes) != set(old_nodes) or edge_signature(target["edges"]) != edge_signature(OLD["edges"]):
        raise RuntimeError(f"Published workflow topology differs: live nodes={sorted(live_nodes)}, baseline nodes={sorted(old_nodes)}, live edges={edge_signature(target['edges'])}, baseline edges={edge_signature(OLD['edges'])}")
    changed = []
    for node_id in old_nodes:
        for attribute in ("name", "intro"):
            before = old_nodes[node_id].get(attribute)
            after = new_nodes[node_id].get(attribute)
            if before != after:
                current = live_nodes[node_id].get(attribute)
                allowed = (before, after)
                if node_id == "lead_intent" and attribute == "intro":
                    allowed += (before.replace("同行、", ""),)
                if current not in allowed:
                    raise RuntimeError(f"Published {node_id}.{attribute} differs from baseline: {current!r} != {before!r}")
                live_nodes[node_id][attribute] = after
                changed.append(f"{node_id}.{attribute}")
        old_inputs = {item["key"]: item["value"] for item in old_nodes[node_id]["inputs"] if "value" in item}
        previous_inputs = {item["key"]: item["value"] for item in previous_nodes[node_id]["inputs"] if "value" in item}
        new_inputs = {item["key"]: item["value"] for item in new_nodes[node_id]["inputs"] if "value" in item}
        for key in old_inputs.keys() & new_inputs.keys():
            if old_inputs[key] == new_inputs[key]:
                continue
            live = node_input(live_nodes[node_id], key)
            if key == "agents" and node_id == "lead_intent":
                for agent in new_inputs[key]:
                    name = agent["key"]
                    old_value = next(item["value"] for item in old_inputs[key] if item["key"] == name)
                    previous_value = next(item["value"] for item in previous_inputs[key] if item["key"] == name)
                    current_agent = next(item for item in live["value"] if item["key"] == name)
                    if current_agent["value"] not in (old_value, previous_value, agent["value"]):
                        raise RuntimeError(f"Published {name} agent differs from baseline")
                    current_agent["value"] = agent["value"]
            elif key == "systemPrompt" and node_id != "lead_intent":
                current = live["value"]
                for index, (before, after) in enumerate(zip(before_paragraphs, after_paragraphs)):
                    if before == after or after in current:
                        continue
                    if before in current:
                        current = current.replace(before, after, 1)
                    elif index == 1:
                        # The live AI nodes no longer duplicate the peer paragraph.
                        continue
                    elif index == 9:
                        before_fields = "可包含 need_type、city、area、phone、wechat、contact_method、contact_value。"
                        after_fields = "可包含 need_type、city、area、phone、wechat、contact_method、contact_value、contact_refused。识别出配置项目时 need_type 必须是该项目的准确名称；仅当客户明确拒绝留资时 contact_refused=true，犹豫或含糊时不要设置。"
                        if after_fields in current:
                            continue
                        if current.count(before_fields) != 1:
                            raise RuntimeError(f"Published {node_id} fields paragraph differs from baseline")
                        current = current.replace(before_fields, after_fields, 1)
                    else:
                        raise RuntimeError(f"Published {node_id} paragraph {index} differs from baseline")
                old_extra = old_inputs[key].rsplit("\n", 1)[-1]
                new_extra = new_inputs[key].rsplit("\n", 1)[-1]
                if old_extra != new_extra and new_extra not in current:
                    if current.count(old_extra) != 1:
                        raise RuntimeError(f"Published {node_id} branch instruction differs from baseline")
                    current = current.replace(old_extra, new_extra, 1)
                live["value"] = current
            else:
                if live["value"] not in (old_inputs[key], previous_inputs[key], new_inputs[key]):
                    raise RuntimeError(f"Published {node_id}.{key} differs from baseline")
                live["value"] = new_inputs[key]
            changed.append(f"{node_id}.{key}")
    variables = target["chatConfig"]["variables"]
    if any(item["key"] == "customer_nickname" for item in variables):
        target["chatConfig"]["variables"] = [item for item in variables
                                              if item["key"] != "customer_nickname"]
        changed.append("chatConfig.customer_nickname")
    if target["nodes"] == published["nodes"] and target["chatConfig"] == published["chatConfig"]:
        print("Contact policy already published")
        return
    print(f"Ready: published version {version}; inputs: {', '.join(changed)}")
    if not args.apply:
        return
    BACKUP.parent.mkdir(exist_ok=True)
    backup_path = BACKUP.with_name(f"fastgpt-contact-policy-backup-{version}.json")
    if backup_path.exists():
        raise RuntimeError(f"Backup already exists: {backup_path}")
    backup_path.write_text(json.dumps({"previous_version_id": version,
        "previous_workflow": published}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = {"nodes": target["nodes"], "edges": target["edges"],
               "chatConfig": target["chatConfig"]}
    request("/api/core/app/version/publish?" + query, {
        **payload, "isPublish": False, "autoSave": True})
    draft = request("/api/core/app/detail?" + query)
    if any(draft[key] != payload[key] for key in payload):
        raise RuntimeError("FastGPT draft did not retain policy changes")
    request("/api/core/app/version/publish?" + query, {
        **payload, "isPublish": True, "versionName": "非同行持续留资邀请"})
    new_app = request("/api/core/app/detail?" + query)
    new_version = new_app.get("publishedVersionId")
    if not new_version or new_version == version:
        raise RuntimeError("Published version did not change")
    actual = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": new_version}))
    if any(actual[key] != payload[key] for key in payload):
        raise RuntimeError("Published policy differs from requested version")
    print(f"Verified published version {new_version}; backup: {backup_path}")


if __name__ == "__main__":
    main()
