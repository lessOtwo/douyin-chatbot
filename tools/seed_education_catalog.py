"""Enter the six readable education/training projects supplied by the user."""
import json
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BUSINESS_URL", "http://127.0.0.1:8088").rstrip("/")
INDUSTRY = "教育教培"
PROJECTS = [
    ("中小学辅导", "您好，请问孩子目前上几年级呢？主要想提升哪一科的成绩？"),
    ("少儿兴趣培训", "您好，请问孩子今年几岁啦？想了解美术、舞蹈、音乐还是其他兴趣课程呢？"),
    ("成人学历提升", "您好，请问您是想提升到大专学历，还是本科学历呢？"),
    ("考公考编", "您好，请问您想了解公务员考试、事业单位考试，还是教师编制培训呢？"),
    ("语言培训", "您好，请问您是想提升日常口语、准备考试，还是工作需要学习语言呢？"),
    ("职业资格考证", "您好，请问您目前想考取哪方面的资格证书呢？"),
]

def local_key():
    key = os.environ.get("BUSINESS_API_KEY")
    if key: return key
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("BUSINESS_API_KEY="):
            return line.split("=", 1)[1]
    raise RuntimeError("BUSINESS_API_KEY is missing")

KEY = local_key()

def request(path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
        headers={"Content-Type":"application/json","x-api-key":KEY})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)

catalog = request("/api/catalog")
industry = next((item for item in catalog["industries"]
    if INDUSTRY in (item["draft_name"], item["published_name"])), None)
if industry is None:
    industry_id = request("/api/catalog/industries", {"name":INDUSTRY})["id"]
    request(f"/api/catalog/industries/{industry_id}/publish", {})
else:
    industry_id = industry["id"]
    if industry["published_name"] != INDUSTRY:
        request("/api/catalog/industries", {"id":industry_id,"name":INDUSTRY})
        request(f"/api/catalog/industries/{industry_id}/publish", {})

for name, question in PROJECTS:
    project = next((item for item in catalog["projects"]
        if item["industry_id"] == industry_id and
        name in (item["draft_name"], item["published_name"])), None)
    if project and project["published_name"] == name and project["published_question"] == question:
        continue
    project_id = request("/api/catalog/projects", {"id":project["id"] if project else None,
        "industry_id":industry_id,"name":name,"question":question})["id"]
    request(f"/api/catalog/projects/{project_id}/publish", {})

published = request("/api/catalog/public")
actual = {item["name"]:item["question"] for item in published["projects"]
    if item["industry_id"] == industry_id}
if any(actual.get(name) != question for name,question in PROJECTS):
    raise RuntimeError("education project readback mismatch")
print(f"已发布行业「{INDUSTRY}」及截图中清晰可读的 {len(PROJECTS)} 个项目。")
