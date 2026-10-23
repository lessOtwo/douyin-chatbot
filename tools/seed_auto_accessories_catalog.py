"""Publish the automotive accessories industry and common inquiry projects."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BUSINESS_URL", "http://127.0.0.1:8088").rstrip("/")
INDUSTRY = "汽车用品"
PROJECTS = [
    ("隐形车衣", "您好，您想贴全车车衣，还是先保护车头等容易剐蹭的部位呢？"),
    ("车窗贴膜", "您好，您是想给新车贴窗膜，还是更换现在的旧膜呢？"),
    ("行车记录仪", "您好，您想了解前后双录，还是基础的前方记录呢？"),
    ("脚垫与座套", "您好，您想看脚垫、座套，还是两样都想配呢？"),
    ("车载电子设备", "您好，您想了解车载充电、手机支架，还是其他车载设备呢？"),
    ("清洁养护用品", "您好，您是想找日常清洁用品，还是漆面、内饰养护用品呢？"),
]


def local_key():
    key = os.environ.get("BUSINESS_API_KEY")
    if key:
        return key
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("BUSINESS_API_KEY="):
            return line.split("=", 1)[1]
    raise RuntimeError("BUSINESS_API_KEY is missing")


KEY = local_key()


def request(path, body=None):
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(BASE + path, data=payload,
                  headers={"Content-Type": "application/json", "x-api-key": KEY})
    with urlopen(req, timeout=15) as response:
        return json.load(response)


catalog = request("/api/catalog")
industry = next((item for item in catalog["industries"]
                 if INDUSTRY in (item["draft_name"], item["published_name"])), None)
if industry is None:
    industry_id = request("/api/catalog/industries", {"name": INDUSTRY})["id"]
    request(f"/api/catalog/industries/{industry_id}/publish", {})
else:
    industry_id = industry["id"]
    if industry["published_name"] != INDUSTRY:
        request(f"/api/catalog/industries/{industry_id}/publish", {})

for name, question in PROJECTS:
    project = next((item for item in catalog["projects"]
                    if item["industry_id"] == industry_id
                    and name in (item["draft_name"], item["published_name"])), None)
    if project is not None:
        if project["published_name"] == name and project["published_question"] == question:
            continue
        raise RuntimeError(f"Existing project {name} differs; review it in the admin UI")
    project_id = request("/api/catalog/projects", {
        "industry_id": industry_id, "name": name, "question": question,
    })["id"]
    request(f"/api/catalog/projects/{project_id}/publish", {})

published = request("/api/catalog/public")
actual = {item["name"]: item["question"] for item in published["projects"]
          if item["industry_id"] == industry_id}
if any(actual.get(name) != question for name, question in PROJECTS):
    raise RuntimeError("Automotive accessories catalog readback mismatch")
print(json.dumps({"industry": INDUSTRY, "projects": PROJECTS}, ensure_ascii=False))
