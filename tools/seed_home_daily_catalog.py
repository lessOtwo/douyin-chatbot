"""Publish the home and daily goods industry and inquiry projects."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BUSINESS_URL", "http://127.0.0.1:8088").rstrip("/")
INDUSTRY = "家居日用"
PROJECTS = [
    ("家具", "您好，您想看沙发、床，还是餐桌等家具呢？"),
    ("家纺", "您好，您是想选床品四件套、被芯枕芯，还是窗帘布艺呢？"),
    ("厨具", "您好，您想找锅具、刀具，还是餐具呢？"),
    ("收纳", "您好，您主要想收纳衣物、厨房用品，还是客厅杂物呢？"),
    ("灯饰", "您好，您想看客厅主灯、卧室灯，还是氛围照明呢？"),
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
    raise RuntimeError("Home and daily goods catalog readback mismatch")
print(json.dumps({"industry": INDUSTRY, "projects": PROJECTS}, ensure_ascii=False))
