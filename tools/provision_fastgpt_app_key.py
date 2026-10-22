"""Create the platform-owned FastGPT API key once and save it to the local .env."""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
lines = ENV.read_text(encoding="utf-8-sig").splitlines()
settings = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
if settings.get("FASTGPT_APP_KEY", "").strip():
    print("平台工作流调用密钥已配置，未创建新密钥。")
    raise SystemExit(0)
password = settings.get("FASTGPT_ROOT_PASSWORD", "")
if not password:
    raise SystemExit("缺少模型平台管理员密码。")
base = os.environ.get("FASTGPT_PROVISION_URL", "http://127.0.0.1:3000").rstrip("/")
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(path, body=None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    with opener.open(urllib.request.Request(
        base + path, data=payload,
        headers={"Content-Type": "application/json", "fastgpt-web-request": "1"},
    ), timeout=30) as response:
        result = json.load(response)
    if result.get("code") != 200:
        raise RuntimeError(f"模型平台请求失败：{path}")
    return result.get("data")


pre = request("/api/support/user/account/preLogin?username=root")
request("/api/support/user/account/loginByPassword", {
    "username": "root",
    "password": hashlib.sha256(password.encode()).hexdigest(),
    "code": pre["code"],
    "language": "zh-CN",
})
key = request("/api/support/openapi/create", {"name": "抖音客服业务服务"})
if not isinstance(key, str) or len(key) < 30 or any(char.isspace() for char in key):
    raise RuntimeError("模型平台返回了无效的调用密钥。")
updated = ["FASTGPT_APP_KEY=" + key if line.startswith("FASTGPT_APP_KEY=") else line for line in lines]
if not any(line.startswith("FASTGPT_APP_KEY=") for line in lines):
    updated.append("FASTGPT_APP_KEY=" + key)
temporary = ENV.with_suffix(".env.tmp")
temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
os.replace(temporary, ENV)
print("平台工作流调用密钥已创建并写入本地配置；请重启业务服务。")
