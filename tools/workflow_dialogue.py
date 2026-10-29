"""Keep one small intent router and concise service dialogue in the workflow."""


CLASSIFIER_PROMPT = (
    "只根据客户私信正文和历史私信分流，不使用昵称。客户主动推销、招揽自己的商品或服务归 peer；"
    "客户本轮首次留下或补全本人联系方式归 lead_provided；其余全部归 normal，包括询问商家联系方式、"
    "拒绝留资、答应稍后提供和已有线索后的联系偏好更正。不要把询问商家号码、引用他人号码、"
    "否定留资或假设号码归为 lead_provided。"
)

CLASSIFIER_AGENTS = [
    {"key": "peer", "value": "客户正文主动推销、招揽自己的商品或服务；不得依据昵称判断"},
    {"key": "lead_provided", "value": "客户本轮首次留下或补全本人电话、微信；不得提取引用或第三方联系方式"},
    {"key": "normal", "value": "其余消息，包括普通咨询、询问商家联系方式、拒绝留资及已有线索后的联系偏好更正"},
]

COMMON_CHANGES = (
    (
        "没有已审核的实时档期依据，不得说该时段可预约或已安排，只能说可帮忙向门店申请或协调，具体时间由门店确认，同一回复不得同时说可预约和待确认。",
        "没有已审核的实时安排依据，不得说该时段可预约或已安排。洗车等到店即时服务若资料未明确要求预约，只把客户给出的时间当作计划到店；说当时店里忙不忙、是否要等位，不说档期、申请或已预约。其他预约型服务的时间由门店确认；没有真实转交和跟进流程时，不承诺已转交或会联系。",
    ),
    ("报价或具体档期无依据时", "报价或具体时间安排无依据时"),
    ("到店/上门服务型有明确需求时直接问希望预约的日期时间", "到店/上门服务型有明确需求时直接问预计到店或上门的日期时间"),
    (
        "进入留资阶段后，分别以“该预约时间是否有空闲工作人员可以接待或上门，需要工作人员与您沟通确认”",
        "进入留资阶段后，分别以“客户计划到店或上门的时间需要进一步确认；洗车只说当时店里忙不忙、是否要等位”",
    ),
    ("不得编造已确认的档期", "不得编造已确认的预约或接待安排"),
    (
        "此后继续由模型自然答疑，每轮变化话术和理由索要联系方式；",
        "此后继续由模型自然答疑；上一轮已索要过联系方式或本轮 contact_request_allowed=false 时不再索要，客户只问地址、营业时间等事实时先直接回答；之后有新的具体沟通理由且允许邀请时再自然索要。",
    ),
    (
        "reply 可说“周六下午2点可帮您向门店申请，档期需确认。”，contact_request 可说“方便留个联系方式，让工作人员确认周六下午2点是否有人可接待您？”",
        "reply 可说“您想周六下午2点来洗车是吧？我这里暂时查不到那时是否要等位。”，contact_request 可说“方便留个联系方式吗？方便后续确认到店时间。”",
    ),
)

ALTERNATE_WASH_EXAMPLE = (
    "reply 可说“您想周六下午2点来洗车是吧？我这里暂时查不到那时是否要等位。”，"
    "contact_request 可说“方便留个联系方式吗？方便后续工作人员和您确认到店时间。”"
)

NORMAL_CHANGES = (
    ("具体价格或预约档期未知时说可向门店确认或申请", "具体价格或到店时间未知时如实说明需门店确认；洗车不说申请或档期"),
    ("该预约时间是否有空闲工作人员可接待或上门", "客户计划到店或上门的时间是否方便安排；洗车说店里忙不忙或是否要等位"),
    ("不编造已确认的档期、折扣或价格", "不编造已确认的时间安排、折扣或价格"),
)


def replace_once(text, old, new):
    count = text.count(old)
    if count == 1:
        return text.replace(old, new, 1)
    if count == 0 and new in text:
        return text
    raise ValueError(f"Unexpected workflow prompt occurrence count for {old[:30]!r}: {count}")


def rewrite_common_prompt(prompt):
    for old, new in COMMON_CHANGES:
        if old == COMMON_CHANGES[-1][0] and old not in prompt and ALTERNATE_WASH_EXAMPLE in prompt:
            continue
        prompt = replace_once(prompt, old, new)
    return prompt


def rewrite_reply_prompt(prompt, node_id):
    prompt = rewrite_common_prompt(prompt)
    if node_id == "normal_reply":
        for old, new in NORMAL_CHANGES:
            if old == "该预约时间是否有空闲工作人员可接待或上门":
                if prompt.count(old) == 2:
                    prompt = prompt.replace(old, new)
                elif prompt.count(old) != 0 or prompt.count(new) != 2:
                    raise ValueError("Unexpected service contact wording count")
            else:
                prompt = replace_once(prompt, old, new)
    marker = "项目选择问句={{project_selection_question}}；"
    if "{{last_contact_request}}" not in prompt or "{{contact_request_allowed}}" not in prompt:
        prompt = replace_once(
            prompt, marker,
            marker + "上一轮业务服务已索要联系方式={{last_contact_request}}；本轮允许邀请留资={{contact_request_allowed}}；",
        )
    return prompt


def reshape_flow(flow):
    removed = {"stop_reply", "contact_reply", "retry_reply"}
    nodes = []
    for node in flow["nodes"]:
        node_id = node["nodeId"]
        if node_id in removed:
            continue
        if node_id == "lead_intent":
            for item in node["inputs"]:
                if item["key"] == "systemPrompt":
                    item["value"] = CLASSIFIER_PROMPT
                elif item["key"] == "agents":
                    item["value"] = CLASSIFIER_AGENTS
        elif node_id in ("lead_received_reply", "normal_reply"):
            for item in node["inputs"]:
                if item["key"] == "systemPrompt":
                    item["value"] = rewrite_reply_prompt(item["value"], node_id)
        nodes.append(node)
    flow["nodes"] = nodes
    flow["edges"] = [edge for edge in flow["edges"]
                     if edge["target"] not in removed and edge["source"] not in removed]
    assert {node["nodeId"] for node in nodes} == {
        "workflowStartNodeId", "lead_intent", "peer_reply", "lead_received_reply", "normal_reply"
    }
    assert len(flow["edges"]) == 4
    return flow
