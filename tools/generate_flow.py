"""Generate an importable Typebot example using the checked-out upstream template schema."""
import copy
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
base = json.loads((root/"Typebot/packages/templates/src/typebots/openai-conditions.json").read_text(encoding="utf-8"))
flow = copy.deepcopy(base)
flow.update(id="renovation-template", name="装修私信 · 业务服务与 FastGPT", publicId=None, workspaceId="workspace", icon="🏠")
variables = ["account_id","conversation_id","internal_key","customer_message","assistant_reply","action"]
flow["variables"] = [{"id":"var-"+v,"name":v,"isSessionVariable":True} for v in variables]
flow["events"] = [{"id":"start","type":"start","outgoingEdgeId":"start-input","graphCoordinates":{"x":0,"y":0}}]
flow["groups"] = [
    {"id":"input","title":"接收客户自然语言","graphCoordinates":{"x":200,"y":0},"blocks":[
        {"id":"customer-input","type":"text input","outgoingEdgeId":"input-understand","options":{"variableId":"var-customer_message"}}
    ]},
    {"id":"understand","title":"业务服务调用 FastGPT","graphCoordinates":{"x":600,"y":0},"blocks":[
        {"id":"understand-http","type":"Webhook","outgoingEdgeId":"understand-branch","options":{
            "isCustomBody":True,"isExecutedOnClient":False,"timeout":40,
            "webhook":{"url":"http://business-service:8080/internal/understand","method":"POST",
                       "headers":[{"id":"content-type","key":"Content-Type","value":"application/json"},
                                  {"id":"internal-key","key":"x-internal-key","value":"{{internal_key}}"}],
                       "body":json.dumps({"account_id":"{{account_id}}","conversation_id":"{{conversation_id}}","text":"{{customer_message}}"},ensure_ascii=False)},
            "responseVariableMapping":[{"id":"map-reply","variableId":"var-assistant_reply","bodyPath":"data.reply"},
                                       {"id":"map-action","variableId":"var-action","bodyPath":"data.action"}]
        }}
    ]},
    {"id":"branch","title":"继续、转人工或停止","graphCoordinates":{"x":1000,"y":0},"blocks":[
        {"id":"action-condition","type":"Condition","outgoingEdgeId":"branch-reply","items":[
            {"id":"handoff-item","outgoingEdgeId":"branch-end","content":{"comparisons":[{"id":"is-handoff","variableId":"var-action","comparisonOperator":"Equal to","value":"handoff"}]}},
            {"id":"stop-item","outgoingEdgeId":"branch-stop","content":{"comparisons":[{"id":"is-stop","variableId":"var-action","comparisonOperator":"Equal to","value":"stop"}]}}
        ]}
    ]},
    {"id":"reply","title":"回复后等待下一轮","graphCoordinates":{"x":1400,"y":0},"blocks":[
        {"id":"reply-text","type":"text","outgoingEdgeId":"reply-input","content":{"richText":[{"type":"p","children":[{"text":"{{assistant_reply}}"}]}]}}
    ]},
    {"id":"end","title":"结束自动流程","graphCoordinates":{"x":1400,"y":400},"blocks":[
        {"id":"end-text","type":"text","content":{"richText":[{"type":"p","children":[{"text":"{{assistant_reply}}"}]}]}}
    ]}
]
flow["edges"] = [
    {"id":"start-input","from":{"eventId":"start"},"to":{"groupId":"input"}},
    {"id":"input-understand","from":{"blockId":"customer-input"},"to":{"groupId":"understand"}},
    {"id":"understand-branch","from":{"blockId":"understand-http"},"to":{"groupId":"branch"}},
    {"id":"branch-reply","from":{"blockId":"action-condition"},"to":{"groupId":"reply"}},
    {"id":"branch-end","from":{"blockId":"action-condition","itemId":"handoff-item"},"to":{"groupId":"end"}},
    {"id":"branch-stop","from":{"blockId":"action-condition","itemId":"stop-item"},"to":{"groupId":"end"}},
    {"id":"reply-input","from":{"blockId":"reply-text"},"to":{"groupId":"input"}}
]
flow["theme"], flow["settings"] = {}, {}
destination = root/"config/typebot-renovation.json"
destination.write_text(json.dumps(flow,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("Generated config/typebot-renovation.json; import and verify against the running Typebot version.")

