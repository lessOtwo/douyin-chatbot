"""Surgically publish model instructions for contact-channel classification."""

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
PROMPT = (ROOT / "config/fastgpt-system-prompt.md").read_text(encoding="utf-8")
FLOW = json.loads((ROOT / "config/fastgpt-lead-workflow.json").read_text(encoding="utf-8"))
NEW = {node["nodeId"]: node for node in FLOW["nodes"]}
OLD_LEAD_EXTRA = "客户本轮明确主动留下本人联系方式。只输出约定 JSON：reply 必须逐字为‘收到，稍后店长联系您详聊。’，action=reply；fields 只包含本次客户消息中本人主动留下的 phone 和/或 wechat，逐字复制。不得提取引用、否定或第三方联系方式。不要追加问题或解释。"
OLD_NORMAL_EXTRA = "先识别客户咨询的项目。新会话需求模糊时使用传入的项目选择问句；识别到主做或其他可做项目后，仅在确有必要时使用该项目的开场问句澄清一次；需求已清楚就跳过提问。客户咨询不做项目时如实说明。随后简短回应并自然索取电话或微信，说明门店和联系用途。不要给具体价格或方案细节。只输出约定 JSON。"
OLD_CLASSIFIER = "根据客户本轮消息和历史判断意图，优先级依次为：明确要求停止联系；客户本轮明确留下本人电话或微信；询问商家联系方式；首次索取后拒绝或暂缓；普通咨询。不要把询问商家电话、引用他人号码、否定留资或假设号码归为收到客户联系方式。"
OLD_NORMAL_AGENT = "普通行业咨询、首次需求澄清或首次留资邀请"


def input_item(node, key):
    return next(item for item in node.get("inputs", []) if item.get("key") == key)


def new_extra(node_id):
    value = input_item(NEW[node_id], "systemPrompt")["value"]
    return value.split("只有已审核商家资料允许作为具体经营事实。\n", 1)[1]


def updated_nodes(nodes):
    count = 0
    new_contact = PROMPT.split("客户主动给出的电话、微信", 1)[1].split("\n\n客户询问商家联系方式时", 1)[0]
    new_fields = PROMPT.split("fields 为对象", 1)[1].split("reply 必须非空", 1)[0]
    for node in nodes:
        node_id = node["nodeId"]
        if node_id == "lead_intent":
            item = input_item(node, "systemPrompt")
            if item["value"] == OLD_CLASSIFIER:
                item["value"] = input_item(NEW[node_id], "systemPrompt")["value"]
                count += 1
            agents = input_item(node, "agents")["value"]
            for agent in agents:
                if agent.get("key") == "normal" and agent.get("value") == OLD_NORMAL_AGENT:
                    agent["value"] = next(v["value"] for v in input_item(NEW[node_id], "agents")["value"] if v["key"] == "normal")
                    count += 1
            continue
        if node_id not in ("lead_received_reply", "contact_reply", "retry_reply", "normal_reply"):
            continue
        item = input_item(node, "systemPrompt")
        value = item["value"]
        start = value.find("客户主动给出的电话、微信只作为候选字段")
        end = value.find("\n\n客户询问商家联系方式时", start)
        if start >= 0 and end > start:
            old_contact = value[start:end]
            if "contact_method" not in old_contact:
                value = value[:start] + "客户主动给出的电话、微信" + new_contact + value[end:]
                count += 1
        start = value.find("fields 为对象")
        end = value.find("reply 必须非空", start)
        if start >= 0 and end > start and "contact_method" not in value[start:end]:
            value = value[:start] + "fields 为对象" + new_fields + value[end:]
            count += 1
        extra = {"lead_received_reply": OLD_LEAD_EXTRA,
                 "normal_reply": OLD_NORMAL_EXTRA}.get(node_id)
        if extra and extra in value and new_extra(node_id) not in value:
            value = value.replace(extra, new_extra(node_id), 1)
            count += 1
        item["value"] = value
    return count


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
    app_query = urllib.parse.urlencode({"appId": APP_ID})
    app = request("/api/core/app/detail?" + app_query)
    old_id = app.get("publishedVersionId")
    if not old_id:
        raise RuntimeError("Target workflow has no published version")
    version = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": old_id}))
    draft_by_id = {node["nodeId"]: node for node in app.get("nodes", [])}
    draft_changes = []
    for node in version["nodes"]:
        draft = draft_by_id.get(node["nodeId"])
        if draft is None:
            draft_changes.append((node["nodeId"], "missing"))
            continue
        for item in node.get("inputs", []):
            key = item.get("key")
            counterpart = next((v for v in draft.get("inputs", []) if v.get("key") == key), None)
            if counterpart is None or counterpart.get("value") != item.get("value"):
                draft_changes.append((node["nodeId"], key))
    if app.get("edges") != version["edges"] or app.get("chatConfig") != version["chatConfig"]:
        draft_changes.append(("workflow", "edges or chatConfig"))
    print(f"Draft differs from published version in {len(draft_changes)} input fields: {draft_changes}")
    print(f"Draft still has {updated_nodes(copy.deepcopy(app.get('nodes', [])))} old fragments")
    count = updated_nodes(version["nodes"])
    if count not in (0, 12):
        raise RuntimeError(f"Expected 12 targeted prompt changes, found {count}; refusing partial publication")
    print(f"Published version {old_id}: {count} targeted changes")
    if not count or not args.apply:
        return
    target_by_id = {node["nodeId"]: node for node in version["nodes"]}
    draft_matches_target = (app.get("edges") == version["edges"] and
        app.get("chatConfig") == version["chatConfig"] and
        set(draft_by_id) == set(target_by_id) and
        all(input_item(draft_by_id[node_id], item["key"]).get("value") == item.get("value")
            for node_id, node in target_by_id.items() for item in node.get("inputs", [])))
    if draft_changes and not draft_matches_target:
        raise RuntimeError("Unpublished draft differs from the targeted edit; refusing to overwrite it")
    if not draft_matches_target:
        request("/api/core/app/version/publish?" + app_query, {
            "nodes": version["nodes"], "edges": version["edges"],
            "chatConfig": version["chatConfig"], "isPublish": False,
            "autoSave": True})
    saved_draft = request("/api/core/app/detail?" + app_query)
    remaining = updated_nodes(saved_draft["nodes"])
    if remaining != 0:
        print(f"Draft readback still matches {remaining} old prompt fragments")
        raise RuntimeError("FastGPT draft did not retain the new channel instructions")
    request("/api/core/app/version/publish?" + app_query, {
        "nodes": version["nodes"], "edges": version["edges"],
        "chatConfig": version["chatConfig"], "isPublish": True,
        "versionName": "联系渠道识别与更正"})
    updated_app = request("/api/core/app/detail?" + app_query)
    new_id = updated_app.get("publishedVersionId")
    if not new_id or new_id == old_id:
        raise RuntimeError("Published version did not change")
    current = request("/api/core/app/version/detail?" + urllib.parse.urlencode({
        "appId": APP_ID, "versionId": new_id}))
    if updated_nodes(current["nodes"]) != 0:
        raise RuntimeError("Published version still contains old lead-channel instructions")
    print(f"Published workflow verified: {new_id}")


if __name__ == "__main__":
    main()
