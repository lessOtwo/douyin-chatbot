"""Publish the focused natural-reply prompt change, with a guarded local rollback."""

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
BASELINE_COMMIT = "30b39cc"
BACKUP = ROOT / "artifacts/fastgpt-natural-replies-backup.json"
CHANGED_NODES = ("lead_received_reply", "contact_reply", "retry_reply", "normal_reply")


def prompt(node):
    return next(item for item in node["inputs"] if item["key"] == "systemPrompt")


def nodes_by_id(flow):
    return {node["nodeId"]: node for node in flow["nodes"]}


def baseline_text(path):
    return subprocess.check_output(
        ["git", "show", f"{BASELINE_COMMIT}:{path}"], cwd=ROOT, encoding="utf-8"
    )


def changed_paragraphs():
    before = baseline_text("config/fastgpt-system-prompt.md").split("\n\n")
    after = (ROOT / "config/fastgpt-system-prompt.md").read_text(encoding="utf-8").split("\n\n")
    if len(before) != len(after):
        raise RuntimeError("System prompt paragraph structure changed")
    return [(old, new) for old, new in zip(before, after) if old != new]


def replace_once(value, old, new, label):
    if value.count(old) != 1:
        raise RuntimeError(f"Published {label} does not contain the expected old wording exactly once")
    return value.replace(old, new, 1)


def assert_same_draft(app, published):
    for key in ("nodes", "edges", "chatConfig"):
        if app[key] != published[key]:
            raise RuntimeError(f"FastGPT has an unpublished {key} change; refusing to overwrite it")


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
        req = urllib.request.Request(
            args.base_url.rstrip("/") + path,
            data=data,
            headers={"Content-Type": "application/json", "fastgpt-web-request": "1"},
        )
        with opener.open(req, timeout=60) as response:
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
    query = urllib.parse.urlencode({"appId": APP_ID})
    app = request("/api/core/app/detail?" + query)
    old_id = app.get("publishedVersionId")
    if not old_id:
        raise RuntimeError("The workflow has no published version")
    published = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": old_id,
    }))
    assert_same_draft(app, published)

    if args.restore:
        backup = json.loads(BACKUP.read_text(encoding="utf-8"))
        if old_id != backup["new_version_id"]:
            raise RuntimeError("Published workflow changed after this experiment; refusing rollback")
        target = backup["previous_workflow"]
        version_name = "恢复自然回复实验前版本"
    else:
        baseline = json.loads(baseline_text("config/fastgpt-lead-workflow.json"))
        updated = json.loads((ROOT / "config/fastgpt-lead-workflow.json").read_text(encoding="utf-8"))
        baseline_nodes = nodes_by_id(baseline)
        updated_nodes = nodes_by_id(updated)
        current_nodes = nodes_by_id(published)
        edge_signature = lambda flow: sorted((edge.get("source"), edge.get("target"), edge.get("sourceHandle"))
                                             for edge in flow["edges"])
        if set(current_nodes) != set(baseline_nodes) or edge_signature(published) != edge_signature(baseline):
            raise RuntimeError("Live published workflow topology differs from baseline: " +
                               f"missing={sorted(set(baseline_nodes) - set(current_nodes))}, " +
                               f"extra={sorted(set(current_nodes) - set(baseline_nodes))}, " +
                               f"live_edges={edge_signature(published)}, " +
                               f"baseline_edges={edge_signature(baseline)}")
        target = copy.deepcopy(published)
        target_nodes = nodes_by_id(target)
        paragraphs = changed_paragraphs()
        if len(paragraphs) != 5:
            raise RuntimeError(f"Expected five focused system-prompt changes, got {len(paragraphs)}")
        for node_id in CHANGED_NODES:
            current_value = prompt(current_nodes[node_id])["value"]
            for index, (old, new) in enumerate(paragraphs, 1):
                current_value = replace_once(current_value, old, new, f"{node_id} paragraph {index}")
            if node_id in ("contact_reply", "normal_reply"):
                marker = "\n客户想要商家联系方式。" if node_id == "contact_reply" else "\n若客户本轮只是更正"
                old_extra = marker + prompt(baseline_nodes[node_id])["value"].split(marker, 1)[1]
                new_extra = marker + prompt(updated_nodes[node_id])["value"].split(marker, 1)[1]
                current_value = replace_once(current_value, old_extra, new_extra, f"{node_id} branch instruction")
            prompt(target_nodes[node_id])["value"] = current_value
        version_name = "自然口语回复实验"

    print(f"Ready: {old_id} -> {version_name}; {len(target['nodes'])} nodes, "
          f"{len(target['edges'])} edges")
    if not args.apply and not args.restore:
        return

    if args.apply:
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        BACKUP.write_text(json.dumps({
            "previous_version_id": old_id,
            "new_version_id": None,
            "previous_workflow": published,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    request("/api/core/app/version/publish?" + query, {
        "nodes": target["nodes"], "edges": target["edges"],
        "chatConfig": target["chatConfig"], "isPublish": True,
        "versionName": version_name,
    })
    new_app = request("/api/core/app/detail?" + query)
    new_id = new_app.get("publishedVersionId")
    if not new_id or new_id == old_id:
        raise RuntimeError("FastGPT did not change the published version")
    actual = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": new_id,
    }))
    for key in ("nodes", "edges", "chatConfig"):
        if actual[key] != target[key]:
            raise RuntimeError(f"Published {key} differs from requested workflow")

    if args.apply:
        BACKUP.write_text(json.dumps({
            "previous_version_id": old_id,
            "new_version_id": new_id,
            "previous_workflow": published,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Rollback snapshot saved: {BACKUP}")
    print(f"Verified published version: {new_id}")


if __name__ == "__main__":
    main()
