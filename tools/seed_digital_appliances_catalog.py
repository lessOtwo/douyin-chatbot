"""Publish the digital products and home appliances catalog."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BUSINESS_URL", "http://127.0.0.1:8088").rstrip("/")
INDUSTRY = "数码家电"
PROJECTS = [
    ("手机", "您好，您想了解拍照好用、续航持久，还是性价比高的手机呢？"),
    ("电脑", "您好，您是想选办公电脑、游戏电脑，还是轻薄便携的型号呢？"),
    ("相机", "您好，您主要想拍人像、视频，还是旅行日常记录呢？"),
    ("家用电器", "您好，您想了解厨房电器、清洁电器，还是空调冰洗这类大家电呢？"),
    ("智能设备", "您好，您想了解智能手表、智能家居，还是其他智能设备呢？"),
    ("3C配件", "您好，您是想找耳机、充电配件，还是手机电脑的其他配件呢？"),
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
    raise RuntimeError("Digital appliances catalog readback mismatch")
print(json.dumps({"industry": INDUSTRY, "projects": PROJECTS}, ensure_ascii=False))
