"""HTTP fixtures only. These are not upstream Typebot or FastGPT runtimes."""
import json
import os
import re
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SESSIONS = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.respond({"fixture": True, "status": "ok"})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/v1/chat/completions":
                text = body["messages"][-1]["content"]
                if "slow-fixture" in text:
                    time.sleep(1)
                action, fields = "reply", {}
                if "转人工" in text:
                    action, reply = "handoff", "已转人工，请稍候。"
                elif "不要联系" in text:
                    action, reply = "stop", "好的，已停止自动联系。"
                elif "不留电话" in text:
                    reply = "可以，我们继续在这里沟通。费用需要结合施工范围确认。"
                elif (phone := re.search(r"(?<!\d)1\d{10}(?!\d)", text)):
                    fields["phone"] = phone.group()
                    reply = "已记录您的联系方式候选，后续需人工确认。"
                elif "老房" in text:
                    fields["need_type"] = "老房翻新"
                    reply = "您主要考虑整体翻新，还是厨房卫生间局部改造？"
                else:
                    reply = "您想了解新房装修、老房翻新，还是软装搭配？"
                self.respond({"choices": [{"message": {"content": json.dumps({
                    "reply": reply, "action": action, "fields": fields
                }, ensure_ascii=False)}}]})
                return
            if self.path.endswith("/startChat"):
                session = str(uuid.uuid4())
                SESSIONS[session] = body["prefilledVariables"]
            elif self.path.endswith("/continueChat"):
                session = self.path.split("/")[-2]
                if session not in SESSIONS:
                    self.respond({"error": "unknown fixture session"}, 404)
                    return
            else:
                self.respond({"error": "unknown fixture endpoint"}, 404)
                return
            variables = SESSIONS[session]
            request = urllib.request.Request(
                os.environ.get("BUSINESS_URL", "http://127.0.0.1:18088") + "/internal/understand",
                data=json.dumps({"account_id": variables["account_id"],
                                 "conversation_id": variables["conversation_id"],
                                 "text": body["message"]["text"]}).encode(),
                headers={"Content-Type": "application/json", "x-internal-key": os.environ["INTERNAL_API_KEY"]},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                decision = json.load(response)
            self.respond({"sessionId": session, "messages": [
                {"type": "text", "content": {"type": "markdown", "markdown": decision["reply"]}}
            ], "input": {"type": "text input"}})
        except Exception as error:
            self.respond({"error": str(error)}, 502)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("MOCK_PORT", "9000"))), Handler).serve_forever()

