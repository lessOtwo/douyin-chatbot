"""Publish the housekeeping services industry and inquiry projects."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BUSINESS_URL", "http://127.0.0.1:8088").rstrip("/")
INDUSTRY = "家政服务"
PROJECTS = [
    ("日常保洁", "您好，您想打扫整套房，还是重点清洁厨房、卫生间呢？"),
    ("母婴护理", "您好，您是想了解产后照护、宝宝照护，还是两方面都需要呢？"),
    ("钟点工", "您好，您需要钟点工做家务、做饭，还是照看老人或孩子呢？"),
    ("养老陪护", "您好，您是需要白天陪伴、夜间照护，还是全天照护呢？"),
    ("家电家居维修", "您好，您是需要维修家电，还是家具、门窗等家居设施呢？"),
    ("宠物托管", "您好，您是需要猫咪、狗狗，还是其他宠物的托管呢？"),
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
    raise RuntimeError("Housekeeping services catalog readback mismatch")
print(json.dumps({"industry": INDUSTRY, "projects": PROJECTS}, ensure_ascii=False))
