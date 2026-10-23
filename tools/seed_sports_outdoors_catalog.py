"""Publish the sports/outdoors industry and common inquiry projects."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BUSINESS_URL", "http://127.0.0.1:8088").rstrip("/")
INDUSTRY = "运动户外"
PROJECTS = [
    ("露营装备", "您好，您是准备当天往返的轻露营，还是要过夜露营呢？"),
    ("徒步登山装备", "您好，您是想看徒步鞋服、背包，还是登山杖等装备呢？"),
    ("骑行装备", "您好，您想找骑行头盔、骑行服，还是车灯等配件呢？"),
    ("跑步装备", "您好，您主要想选跑鞋，还是跑步服饰和配件呢？"),
    ("球类用品", "您好，您主要玩羽毛球、篮球，还是其他球类运动呢？"),
    ("健身器材", "您好，您是想在家锻炼，还是给训练场地添置器材呢？"),
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
    raise RuntimeError("Sports/outdoors catalog readback mismatch")
print(json.dumps({"industry": INDUSTRY, "projects": PROJECTS}, ensure_ascii=False))
