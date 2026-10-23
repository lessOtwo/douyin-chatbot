"""Build a FastGPT v4.17.2 lead-generation workflow and optionally install a draft."""

import argparse
import copy
from datetime import date
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import uuid
import urllib.parse
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "FastGPT/packages/service/test/core/workflow/simple.json"
OUTPUT = ROOT / "config/fastgpt-lead-workflow.json"
RECORD = ROOT / "config/fastgpt-workflow-draft.json"
SMOKE_RECORD = ROOT / "artifacts/fastgpt-workflow-smoke.json"


def edge(source, target, handle="right"):
    return {"source": source, "target": target,
            "sourceHandle": f"{source}-source-{handle}",
            "targetHandle": f"{target}-target-left"}


def build(model):
    sample = json.loads(SOURCE.read_text(encoding="utf-8"))
    start = copy.deepcopy(next(node for node in sample["nodes"] if node["flowNodeType"] == "workflowStart"))
    ai_template = next(node for node in sample["nodes"] if node["flowNodeType"] == "chatNode")
    prompt = (ROOT / "config/fastgpt-system-prompt.md").read_text(encoding="utf-8")
    prompt += "\n当前商家：merchant_id={{merchant_id}}；行业={{industry}}；项目选择问句={{project_selection_question}}；已审核商家资料及项目配置={{merchant_profile}}。只有已审核商家资料允许作为具体经营事实。"

    classify = {
        "nodeId": "lead_intent", "name": "识别客户意图", "intro": "同行、停止、收到客户联系方式、索取商家联系方式、二次留资或普通咨询",
        "avatar": "core/workflow/template/questionClassify", "flowNodeType": "classifyQuestion",
        "position": {"x": 920, "y": 260}, "version": "4.9.2", "showStatus": True,
        "inputs": [
            {"key": "model", "renderTypeList": ["selectLLMModel", "reference"],
             "valueType": "string", "label": "模型", "value": model, "required": True},
            {"key": "systemPrompt", "renderTypeList": ["textarea", "reference"],
             "valueType": "string", "label": "分类背景", "value": "当前商家行业={{industry}}；客户昵称={{customer_nickname}}。先综合昵称、本轮消息和历史判断对方身份。同行包括同一或相关行业的商家、服务商、安装施工方；昵称明确表达经营或招揽身份时，即使消息写得像找服务、附上手机号或请人联系，也优先归同行，不能让留资覆盖同行。例如家政商家遇到昵称‘泰安月嫂找红姐’，消息‘需要日间夜间月嫂联系我，13800000000’，应归同行；装修门店遇到木门安装商也归同行。普通消费者昵称只是个人需求描述（如‘想找月嫂的张女士’）、转述别人身份，或仅含行业词但无经营身份，不据此归同行；不确定时走其他分类。其余优先级：明确要求停止联系；客户本轮明确留下本人电话或微信；更正此前已留号码的联系渠道；询问商家联系方式；首次索取后拒绝或暂缓；普通咨询。客户后续说‘不要打电话’或‘加我微信’是联系渠道更正，归普通咨询，不能归二次留资；不要把询问商家电话、引用他人号码、否定留资或假设号码归为收到客户联系方式。"},
            {"key": "history", "renderTypeList": ["numberInput", "reference"],
             "valueType": "chatHistory", "label": "对话历史", "value": 20, "required": True},
            {"key": "userChatInput", "renderTypeList": ["reference", "textarea"],
             "valueType": "string", "label": "客户消息", "value": ["workflowStartNodeId", "userChatInput"], "required": True},
            {"key": "agents", "renderTypeList": ["custom"], "valueType": "any", "label": "分类",
             "value": [
                 {"key": "peer", "value": "同一或相关行业的商家、服务商、安装施工方；昵称表明经营或招揽身份时即使消息像咨询、留了手机号也优先归此类，如家政商家遇到‘泰安月嫂找红姐’留号求联系"},
                 {"key": "stop", "value": "客户明确要求停止联系或不再收到回复"},
                 {"key": "lead_provided", "value": "客户本轮明确留下本人电话或微信，例如‘我的电话是13800000000’或‘我的微信是abc12345’"},
                 {"key": "merchant_contact", "value": "客户询问商家微信或电话号码"},
                 {"key": "lead_retry", "value": "已有首次留资邀请后拒绝或暂缓，且未要求停止联系"},
                 {"key": "normal", "value": "普通行业咨询、首次需求澄清、首次留资邀请，或已留手机号后的联系渠道更正"},
             ]},
        ],
        "outputs": [{"id": "cqResult", "key": "cqResult", "type": "static",
                     "valueType": "string", "label": "分类结果", "required": True}],
    }

    def ai(node_id, name, x, y, extra):
        node = copy.deepcopy(ai_template)
        node.update({"nodeId": node_id, "name": name, "position": {"x": x, "y": y}})
        for item in node["inputs"]:
            if item["key"] == "model":
                item["value"] = model
            elif item["key"] == "systemPrompt":
                item["value"] = prompt + "\n" + extra
            elif item["key"] == "history":
                item["value"] = 20
            elif item["key"] == "maxToken":
                item["value"] = 1200
        return node

    stop = {
        "nodeId": "stop_reply", "name": "停止联系确认", "intro": "返回结构化 stop 决策",
        "avatar": "core/workflow/template/reply", "flowNodeType": "answerNode",
        "position": {"x": 1510, "y": -120},
        "inputs": [{"key": "text", "renderTypeList": ["textarea", "reference"],
                    "valueType": "any", "label": "回复内容", "required": True,
                    "value": '{"reply":"好的，之后不再联系您。","action":"stop","fields":{}}'}],
        "outputs": [],
    }
    peer = {
        "nodeId": "peer_reply", "name": "同行固定回复", "intro": "同行不进入留资分支",
        "avatar": "core/workflow/template/reply", "flowNodeType": "answerNode",
        "position": {"x": 1510, "y": -380},
        "inputs": [{"key": "text", "renderTypeList": ["textarea", "reference"],
                    "valueType": "any", "label": "回复内容", "required": True,
                    "value": '{"reply":"不用呢 谢谢","action":"peer","fields":{}}'}],
        "outputs": [],
    }
    nodes = [
        start, classify, peer, stop,
        ai("lead_received_reply", "收到客户联系方式", 1510, 130,
           "客户本轮明确主动留下本人联系方式。只输出约定 JSON：reply 必须逐字为‘收到，稍后店长联系您详聊。’，action=reply；fields 中的 phone 和/或 wechat 只取本轮本人主动留下的原文，并给出 contact_method。客户说‘不要打电话’或‘加我微信’且只留下手机号时，phone 保留手机号、contact_method=wechat，不要伪造独立微信号。不得提取引用、否定或第三方联系方式。不要追加问题或解释。"),
        ai("contact_reply", "客户索取商家联系方式", 1510, 390,
           "客户想要商家联系方式。若已审核商家资料允许公开门店电话且有号码，直接给号码，不附带索取客户联系方式、营业时间或其他无关信息；否则简短说明暂时无法提供，可继续在本渠道咨询，或自愿留电话、微信让门店联系。不得编造号码。输出约定 JSON。"),
        ai("retry_reply", "第二次留资兜底", 1510, 650,
           "这是一次二次留资机会。若客户已要求停止联系或已二次拒绝，不再索取。否则使用配置的兜底话术：我这个是商家号，线上发联系方式出去会封号，您的联系方式方便留一下吗？我让门店加您。只输出约定 JSON。"),
        ai("normal_reply", "行业答疑与首次留资", 1510, 910,
           "若客户本轮只是更正此前已留手机号的联系渠道（如‘别打电话，加我微信’），只简短确认偏好，fields 返回 contact_method=wechat 及历史中对应的 contact_value，不再索要联系方式；否则先识别客户咨询的项目。新会话需求模糊时使用传入的项目选择问句；识别到主做或其他可做项目后，仅在确有必要时使用该项目的开场问句澄清一次；需求已清楚就跳过提问。客户咨询不做项目时如实说明。每轮先直接回答本轮问题，用口语短句，不重复已问过的留资邀请。客户需要具体报价或后续服务、此前未索取过联系方式时，再自然问一次电话或微信，说清由店里老师答复即可，最多两句，不再补重复的联系承诺。不要给具体价格或方案细节。只输出约定 JSON。"),
    ]
    edges = [edge("workflowStartNodeId", "lead_intent"),
             edge("lead_intent", "peer_reply", "peer"),
             edge("lead_intent", "stop_reply", "stop"),
             edge("lead_intent", "lead_received_reply", "lead_provided"),
             edge("lead_intent", "contact_reply", "merchant_contact"),
             edge("lead_intent", "retry_reply", "lead_retry"),
             edge("lead_intent", "normal_reply", "normal")]
    chat_config = {"variables": [
        {"key": key, "label": label, "description": "业务服务提供", "type": "custom", "valueType": "string", "defaultValue": ""}
        for key, label in [
            ("merchant_id", "商家编号"), ("industry", "所属行业"),
            ("project_selection_question", "项目选择问句"),
            ("merchant_profile", "已审核商家资料"),
            ("customer_nickname", "客户昵称")
        ]
    ]}
    return {"name": "抖音私信留资工作流（草稿）", "type": "advanced",
            "intro": "按商家资料答疑，主动留资，二次兜底；需绑定真实模型与商家资料后验收",
            "nodes": nodes, "edges": edges, "chatConfig": chat_config}


def env_password():
    value = os.environ.get("FASTGPT_ROOT_PASSWORD", "")
    if value:
        return value
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("FASTGPT_ROOT_PASSWORD="):
            return line.split("=", 1)[1]
    raise RuntimeError("FASTGPT_ROOT_PASSWORD is missing")


def install_draft(flow, base, model, smoke_test=False):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                     headers={"Content-Type": "application/json",
                                              "fastgpt-web-request": "1"})
        try:
            with opener.open(req, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError(f"FastGPT API HTTP {error.code} at {path}: {detail[:1200]}") from None
        if result.get("code") != 200:
            raise RuntimeError(f"FastGPT API failed at {path}: {result.get('code')} {result.get('msg', '')}")
        return result.get("data")

    pre = request("/api/support/user/account/preLogin?" + urllib.parse.urlencode({"username": "root"}))
    hashed = hashlib.sha256(env_password().encode("utf-8")).hexdigest()
    request("/api/support/user/account/loginByPassword",
            {"username": "root", "password": hashed, "code": pre["code"], "language": "zh-CN"})
    catalog = request("/api/core/ai/model/catalog")
    llm_models = [item for item in catalog["data"]["models"]
                  if item.get("type") == "llm"]
    match = next((item for item in llm_models
                  if model in (item.get("model"), item.get("name"))), None)
    if match is None:
        names = [(item.get("model"), item.get("name")) for item in llm_models]
        raise RuntimeError(f"configured LLM {model!r} is not available to root: {names}")
    for node in flow["nodes"]:
        for item in node.get("inputs", []):
            if item.get("key") == "model":
                item["key"] = "modelId"
                item["value"] = match["modelId"]
    if RECORD.exists():
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        if record.get("published") or not record.get("appId"):
            raise RuntimeError("existing workflow record is published or invalid; refusing to overwrite")
        app_id = record["appId"]
        request("/api/core/app/version/publish?" + urllib.parse.urlencode({"appId": app_id}),
                {"nodes": flow["nodes"], "edges": flow["edges"],
                 "chatConfig": flow["chatConfig"], "isPublish": False, "autoSave": True})
    else:
        app_id = request("/api/core/app/create", flow)
    detail = request("/api/core/app/detail?" + urllib.parse.urlencode({"appId": app_id}))
    expected = {node["nodeId"] for node in flow["nodes"]}
    actual = {node["nodeId"] for node in detail["nodes"]}
    if actual != expected or len(detail["edges"]) != len(flow["edges"]):
        raise RuntimeError("FastGPT draft node/edge readback does not match generated workflow")
    configured_variables = {item["key"] for item in detail.get("chatConfig", {}).get("variables", [])}
    if configured_variables != {item["key"] for item in flow["chatConfig"]["variables"]}:
        raise RuntimeError("FastGPT draft did not retain merchant context variables")
    models = [item.get("value") for node in detail["nodes"]
              for item in node.get("inputs", []) if item.get("key") in ("model", "modelId")]
    if len(models) != 5 or any(not value for value in models):
        raise RuntimeError(f"FastGPT draft has missing model references: {models}")
    if any(value != match["modelId"] for value in models):
        raise RuntimeError("FastGPT draft did not retain the selected model ID")
    stop_input = next(item for node in detail["nodes"] if node["nodeId"] == "stop_reply"
                      for item in node.get("inputs", []) if item.get("key") == "text")
    if not stop_input.get("value"):
        raise RuntimeError("FastGPT draft lost the stop reply text")
    lead_received_prompt = next(item.get("value", "") for node in detail["nodes"]
                                if node["nodeId"] == "lead_received_reply"
                                for item in node.get("inputs", []) if item.get("key") == "systemPrompt")
    if "收到，稍后店长联系您详聊。" not in lead_received_prompt or not any(
            edge_item["source"] == "lead_intent" and
            edge_item["target"] == "lead_received_reply" and
            edge_item["sourceHandle"] == "lead_intent-source-lead_provided"
            for edge_item in detail["edges"]):
        raise RuntimeError("FastGPT draft lost the lead-received branch or its fixed reply")
    RECORD.write_text(json.dumps({"appId": app_id, "name": flow["name"],
                                  "status": "draft", "model": model,
                                  "nodes": len(actual), "edges": len(detail["edges"]),
                                  "published": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if smoke_test:
        chat_id = "script-smoke-" + uuid.uuid4().hex

        def run_smoke(message, alternate_chat_id=None):
            payload = {"appId": app_id, "appName": flow["name"], "chatId": alternate_chat_id or chat_id,
                       "messages": [{"role": "user", "content": message}],
                       "nodes": detail["nodes"], "edges": detail["edges"],
                       "chatConfig": detail.get("chatConfig", {}),
                       "variables": {"merchant_id": "smoke-test", "industry": "装修装饰",
                                     "project_selection_question": "您好，您想咨询新房装修、老房翻新，还是其他相关需求？",
                                     "merchant_profile": json.dumps({"fields":{"完整门店名称":"演示门店","所属行业":"装修装饰","服务区域":"青岛市","资料或服务":"提供装修资料并预约门店沟通"},"projects":{"main":[{"name":"新房装修","question":"您的新房准备做全屋装修还是局部装修？"},{"name":"老房翻新","question":"您想做局部改造还是整屋翻新？"}],"available":[],"excluded":[{"name":"工装","question":""}]}},ensure_ascii=False)}}
            req = urllib.request.Request(base.rstrip("/") + "/api/core/chat/chatTest",
                                         data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                         headers={"Content-Type": "application/json",
                                                  "fastgpt-web-request": "1"})
            try:
                with opener.open(req, timeout=120) as response:
                    raw = response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as error:
                error_detail = error.read().decode("utf-8", "replace")
                raise RuntimeError(f"FastGPT smoke test HTTP {error.code}: {error_detail[:1200]}") from None
            answer_parts = []
            for block in raw.replace("\r\n", "\n").split("\n\n"):
                if not (block.startswith("event: answer\n") or
                        block.startswith("event: fastAnswer\n")):
                    continue
                data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), "")
                if not data or data == "[DONE]":
                    continue
                event = json.loads(data)
                part = event.get("choices", [{}])[0].get("delta", {}).get("content")
                if isinstance(part, str):
                    answer_parts.append(part)
            try:
                decision = json.loads("".join(answer_parts))
            except json.JSONDecodeError:
                node_events = [block[:600] for block in raw.replace("\r\n", "\n").split("\n\n")
                               if block.startswith("event: flowNodeResponse")]
                raise RuntimeError("FastGPT smoke test did not return decision JSON: " +
                                   repr("".join(answer_parts)[:300]) + " nodes=" +
                                   repr(node_events[-2:])) from None
            if decision.get("action") not in ("reply", "unanswered", "stop") or not decision.get("reply"):
                raise RuntimeError("FastGPT smoke test decision is missing action or reply")
            return decision

        lead = run_smoke("青岛的老房翻新，大约90平，想拿项目清单，怎么获取？")
        if lead["action"] != "reply" or not any(word in lead["reply"] for word in ("电话", "微信", "联系方式")):
            raise RuntimeError("FastGPT smoke test did not ask for customer contact: " + repr(lead))
        vague = run_smoke("你好", "script-project-vague-" + uuid.uuid4().hex)
        if vague["action"] != "reply" or not any(word in vague["reply"] for word in ("新房装修", "老房翻新")):
            raise RuntimeError("FastGPT smoke test did not offer merchant main projects: " + repr(vague))
        specific = run_smoke("我想了解老房翻新", "script-project-specific-" + uuid.uuid4().hex)
        if specific["action"] != "reply" or not any(word in specific["reply"] for word in ("局部", "整屋")):
            raise RuntimeError("FastGPT smoke test did not use the matched project question: " + repr(specific))
        provided = run_smoke("我的电话是13800000000")
        if (provided["action"] != "reply" or provided["reply"] != "收到，稍后店长联系您详聊。" or
                provided.get("fields", {}).get("phone") != "13800000000"):
            raise RuntimeError("FastGPT smoke test did not acknowledge and extract customer contact: " + repr(provided))
        stopped = run_smoke("不要再联系我")
        if stopped["action"] != "stop":
            raise RuntimeError("FastGPT smoke test did not follow stop branch")
        SMOKE_RECORD.parent.mkdir(parents=True, exist_ok=True)
        SMOKE_RECORD.write_text(json.dumps({
            "date": date.today().isoformat(), "result": "passed",
            "appId": app_id, "model": model, "scope": "local FastGPT chatTest draft, three turns on one chatId plus two separate project scenarios",
            "checks": ["vague inquiry offers main projects", "specific inquiry uses its project question", "lead invitation asks for customer contact", "contact reply exactly matches fixed text and extracts phone", "stop branch returns stop JSON"],
            "published": False, "business_service_bound": False,
            "real_channel_verified": False, "full_tests_run": False,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("FastGPT smoke test passed: project selection, matched project, lead invitation, contact acknowledgement and stop branch")
    return app_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.8-flash", help="FastGPT model name in this instance")
    parser.add_argument("--install-draft", action="store_true", help="create or update a draft in the local FastGPT instance")
    parser.add_argument("--smoke-test", action="store_true", help="run one real model request on the draft")
    parser.add_argument("--base-url", default="http://localhost:3000")
    args = parser.parse_args()
    flow = build(args.model)
    OUTPUT.write_text(json.dumps(flow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {OUTPUT} ({len(flow['nodes'])} nodes, {len(flow['edges'])} edges)")
    if args.install_draft:
        print("verified FastGPT draft appId=" + str(install_draft(flow, args.base_url, args.model, args.smoke_test)))
