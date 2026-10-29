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
    prompt += "\n当前商家：merchant_id={{merchant_id}}；行业={{industry}}；项目选择问句={{project_selection_question}}；上一轮业务服务已索要联系方式={{last_contact_request}}；本轮允许邀请留资={{contact_request_allowed}}；已审核商家资料及项目配置={{merchant_profile}}。只有已审核商家资料允许作为具体经营事实。"

    classify = {
        "nodeId": "lead_intent", "name": "识别客户意图", "intro": "仅根据私信识别同行和客户本人留资",
        "avatar": "core/workflow/template/questionClassify", "flowNodeType": "classifyQuestion",
        "position": {"x": 920, "y": 260}, "version": "4.9.2", "showStatus": True,
        "inputs": [
            {"key": "model", "renderTypeList": ["selectLLMModel", "reference"],
             "valueType": "string", "label": "模型", "value": model, "required": True},
            {"key": "systemPrompt", "renderTypeList": ["textarea", "reference"],
             "valueType": "string", "label": "分类背景", "value": "只根据客户私信正文和历史私信判断是否为同行，不使用昵称。只有客户在正文中主动推销、招揽自己的商品或服务才归 peer，不受行业是否相同影响。客户本轮主动留下本人联系方式归 lead_provided；明确答应稍后提供本人联系方式归 normal；询问商家联系方式归 merchant_contact；拒绝留资或要求停止联系等尚未留资消息归 lead_retry；其余非同行消息归 normal。不要使用 stop。不要把询问商家号码、引用他人号码、否定留资或假设号码归为客户已留资。"},
            {"key": "history", "renderTypeList": ["numberInput", "reference"],
             "valueType": "chatHistory", "label": "对话历史", "value": 20, "required": True},
            {"key": "userChatInput", "renderTypeList": ["reference", "textarea"],
             "valueType": "string", "label": "客户消息", "value": ["workflowStartNodeId", "userChatInput"], "required": True},
            {"key": "agents", "renderTypeList": ["custom"], "valueType": "any", "label": "分类",
             "value": [
                 {"key": "peer", "value": "客户私信正文主动推销或招揽自己的商家、服务业务，即使与当前商家行业不同也归此类；不得依据昵称判断"},
                {"key": "stop", "value": "不使用此分类；停止联系请求按非同行消息继续答疑，由业务服务在未留资时追加邀请"},
                 {"key": "lead_provided", "value": "客户本轮明确留下本人电话或微信，例如‘我的电话是13800000000’或‘我的微信是abc12345’"},
                 {"key": "merchant_contact", "value": "客户询问商家微信或电话号码"},
                {"key": "lead_retry", "value": "尚未留资客户拒绝留资、要求停止联系、犹豫或要求别再问；仍继续答疑，由业务服务在末尾追加联系方式邀请"},
                 {"key": "normal", "value": "其余非同行消息，包括普通咨询、询价、项目问题或已留资后的联系渠道更正"},
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
        "nodeId": "stop_reply", "name": "非同行答疑兜底", "intro": "若分类误入此分支仍继续会话",
        "avatar": "core/workflow/template/reply", "flowNodeType": "answerNode",
        "position": {"x": 1510, "y": -120},
        "inputs": [{"key": "text", "renderTypeList": ["textarea", "reference"],
                    "valueType": "any", "label": "回复内容", "required": True,
                    "value": '{"reply":"您想了解哪个项目？","action":"reply","fields":{}}'}],
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
        ai("lead_received_reply", "首次留资确认", 1510, 130,
           "客户本轮明确主动留下本人联系方式。只输出约定 JSON：contact_method=wechat 时 reply 必须逐字为‘好的，稍后加您沟通，留意通过下’；contact_method=phone 时 reply 必须逐字为‘好的，稍后工作人员会给您致电沟通’，action=reply；fields 中的 phone 和/或 wechat 只取本轮本人主动留下的原文，并给出 contact_method。客户说‘不要打电话’或‘加我微信’且只留下手机号时，phone 保留手机号、contact_method=wechat，不要伪造独立微信号。不得提取引用、否定或第三方联系方式。不要追加问题或解释。"),
        ai("contact_reply", "商家联系方式答疑", 1510, 390,
           "先回答客户询问的商家联系方式。仅当已审核资料允许公开且有号码时提供号码，否则说明暂时无法提供，可在本渠道继续咨询。reply 不出现电话、微信二词；仅在已进入留资阶段且 contact_request_allowed=true 时另写符合话术类别和当前阶段的 contact_request，不把它写入 reply；阶段提问时为空。只输出约定 JSON。"),
        ai("retry_reply", "拒绝后继续答疑", 1510, 650,
           "客户拒绝留资、犹豫或要求停止联系时，仍针对本轮问题简短答复；能做的项目直接回答具体需求，只有确定不能做才在开头说明；未经确认的预约时间只能说可帮忙申请或协调，不能承诺该时段有空位。不设置 contact_refused，不返回 stop。遵循历史和话术类别：服务型尚未获知预约时间、批发型尚未问购买数量、零售型尚未问型号或档次时只问下一项，不索要；进入留资阶段且 contact_request_allowed=true 时另写自然变化的 contact_request；否则为空字符串。只输出约定 JSON。"),
        ai("normal_reply", "商家项目答疑", 1510, 910,
           "先根据已审核商家资料和项目分组回答当前问题；能做的项目直接回答具体需求，不用“某项目可做”作开头；只有 excluded 或未配置项目才在开头说明不能做。具体价格或预约档期未知时说可向门店确认或申请，不把指定时段说成已可预约。只有 main、available 是本店可做项目；不要漏答客户当前问题。客户仅打招呼或需求不明时，按 main、available 项目引导并设置 fields.project_selection_only=true，不索要。客户有明确需求时按已审核“话术类别”单选值和历史推进：到店/上门服务型有明确需求时直接问预约时间，不先问服务目标细分；工厂批发型先问购买数量；零售型先问不同型号或档次。旧资料“资料或服务=预约时间/折扣”分别按服务型/批发型处理，其他旧资料才按实际需求推断。已给的信息直接沿用；问阶段问题时设置 fields.intake_question_only=true，contact_request 为空；每轮只问一项。进入留资阶段后，分别以该预约时间是否有空闲工作人员可接待或上门、该数量的折扣、不同型号或档次的差异和特性需要门店或工作人员与客户沟通为理由索要联系方式。reply 仍先答复客户问题；零售型先简述有依据的差异或特点，再由工作人员详细沟通。具体参数不明时如实说明需核对，不编造型号规格；不编造已确认的档期、折扣或价格。客户明确答应稍后提供本人联系方式时设置 fields.contact_promised=true，contact_request 为空；犹豫或考虑时不设置。识别到可做项目时 fields.need_type 写准确名称。已留资后继续答疑，联系渠道更正可返回 contact_method、contact_value。完成上述阶段后，若 contact_request_allowed=true，每轮自由答疑并另写变化表达和理由的 contact_request；不返回 stop。只输出约定 JSON。"),
    ]
    normal_reply = next(node for node in nodes if node["nodeId"] == "normal_reply")
    normal_prompt = next(item for item in normal_reply["inputs"] if item["key"] == "systemPrompt")
    normal_prompt["value"] += "\n各话术类别的 contact_request 必须点明具体沟通事项：服务型为该预约时间是否有空闲工作人员可接待或上门，批发型为折扣，零售型为不同型号或档次的差异和特性；同时点明由门店或工作人员与客户沟通。不能只写‘详细沟通’。"
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
            ("last_contact_request", "上轮是否索要联系方式"),
            ("contact_request_allowed", "本轮是否允许邀请留资"),
            ("merchant_profile", "已审核商家资料")
        ]
    ]}
    return {"name": "抖音私信留资工作流（草稿）", "type": "advanced",
            "intro": "按已审核项目资料答疑，未留资时在末尾邀请留下联系方式",
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
    if not all(reply in lead_received_prompt for reply in ("好的，稍后加您沟通，留意通过下", "好的，稍后工作人员会给您致电沟通")) or not any(
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
                                     "merchant_profile": json.dumps({"fields":{"完整门店名称":"演示门店","所属行业":"装修装饰","服务区域":"青岛市","话术类别":"到店/上门服务型"},"projects":{"main":[{"name":"新房装修","question":"您的新房准备做全屋装修还是局部装修？"},{"name":"老房翻新","question":"您想做局部改造还是整屋翻新？"}],"available":[],"excluded":[{"name":"工装","question":""}]}},ensure_ascii=False)}}
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
            if decision.get("action") not in ("reply", "peer") or not decision.get("reply"):
                raise RuntimeError("FastGPT smoke test decision is missing action or reply")
            return decision

        lead = run_smoke("青岛的老房翻新，大约90平，想拿项目清单，怎么获取？")
        if lead["action"] != "reply" or not any(word in lead["reply"] for word in ("老房", "翻新")):
            raise RuntimeError("FastGPT smoke test did not answer project inquiry: " + repr(lead))
        vague = run_smoke("你好", "script-project-vague-" + uuid.uuid4().hex)
        if vague["action"] != "reply" or not any(word in vague["reply"] for word in ("新房装修", "老房翻新")):
            raise RuntimeError("FastGPT smoke test did not offer configured projects: " + repr(vague))
        specific = run_smoke("我想了解老房翻新", "script-project-specific-" + uuid.uuid4().hex)
        if specific["action"] != "reply" or "老房翻新" not in specific["reply"]:
            raise RuntimeError("FastGPT smoke test did not answer matched project: " + repr(specific))
        provided = run_smoke("我的电话是13800000000")
        if (provided["action"] != "reply" or provided["reply"] != "好的，稍后工作人员会给您致电沟通" or
                provided.get("fields", {}).get("phone") != "13800000000"):
            raise RuntimeError("FastGPT smoke test did not acknowledge and extract customer contact: " + repr(provided))
        stopped = run_smoke("不要再联系我")
        if stopped["action"] != "reply" or "不再联系" in stopped["reply"]:
            raise RuntimeError("FastGPT smoke test stopped instead of continuing: " + repr(stopped))
        SMOKE_RECORD.parent.mkdir(parents=True, exist_ok=True)
        SMOKE_RECORD.write_text(json.dumps({
            "date": date.today().isoformat(), "result": "passed",
            "appId": app_id, "model": model, "scope": "local FastGPT chatTest draft, three turns on one chatId plus two separate project scenarios",
            "checks": ["vague and project inquiry answered from configured projects", "provided contact acknowledged and extracted", "stop request did not stop"],
            "published": False, "business_service_bound": False,
            "real_channel_verified": False, "full_tests_run": False,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("FastGPT smoke test passed: configured project answers, contact acknowledgement and continued conversation after stop request")
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
