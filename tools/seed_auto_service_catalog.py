"""Add and publish the automotive-service industry and its four inquiry projects."""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BUSINESS_URL", "http://127.0.0.1:8088").rstrip("/")
INDUSTRY = "汽车服务"
PROJECTS = [
    ("日常保养", "您好，车辆目前想做哪方面的日常保养？"),
    ("美容护理", "您好，您想做车身外观护理，还是内饰清洁？"),
    ("检测维护", "您好，您是想做定期检测，还是排查车辆异常？"),
    ("专项维修", "您好，车辆哪里需要维修？方便说下故障现象吗？"),
]


def api_key():
    if os.environ.get("BUSINESS_API_KEY"):
        return os.environ["BUSINESS_API_KEY"]
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("BUSINESS_API_KEY="):
            return line.split("=", 1)[1]
    raise RuntimeError("BUSINESS_API_KEY is missing")


KEY = api_key()


def request(path, body=None):
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    with urlopen(Request(BASE + path, data=payload,
            headers={"Content-Type": "application/json", "x-api-key": KEY}), timeout=15) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    catalog = request("/api/catalog")
    matches = [item for item in catalog["industries"]
               if INDUSTRY in (item["draft_name"], item["published_name"])]
    if len(matches) > 1:
        raise RuntimeError("Multiple automotive-service industries exist; review before updating")
    industry = matches[0] if matches else None
    if industry and industry["published_name"] not in (None, INDUSTRY):
        raise RuntimeError("The matching industry has a different published name")
    industry_id = industry["id"] if industry else None
    existing = [item for item in catalog["projects"] if item["industry_id"] == industry_id] if industry_id else []
    if existing and any(item["draft_name"] not in dict(PROJECTS)
                        and item["published_name"] not in dict(PROJECTS) for item in existing):
        raise RuntimeError("The industry has other projects; review before updating")
    project_plan = []
    for name, question in PROJECTS:
        found = [item for item in existing if name in (item["draft_name"], item["published_name"])]
        if len(found) > 1:
            raise RuntimeError(f"Multiple {name} projects exist; review before updating")
        item = found[0] if found else None
        if item and item["published_name"] == name and item["published_question"] == question:
            action = "already published"
        elif (item and item["draft_name"] == name and item["draft_question"] == question
                and item["published_name"] is None):
            action = "publish draft"
        elif item:
            raise RuntimeError(f"Existing {name} project differs; review before updating")
        else:
            action = "create and publish"
        project_plan.append((name, question, item, action))
    print(json.dumps({"industry": INDUSTRY,
        "industry_action": "create and publish" if not industry else
            ("publish draft" if industry["published_name"] is None else "already published"),
        "projects": [{"name": name, "question": question, "action": action}
                     for name, question, _, action in project_plan],
        "mode": "apply" if args.apply else "dry-run"}, ensure_ascii=False))
    if not args.apply:
        return

    if industry_id is None:
        industry_id = request("/api/catalog/industries", {"name": INDUSTRY})["id"]
    if not industry or industry["published_name"] is None:
        request(f"/api/catalog/industries/{industry_id}/publish", {})
    for name, question, item, action in project_plan:
        if action == "already published":
            continue
        project_id = item["id"] if item else request("/api/catalog/projects", {
            "industry_id": industry_id, "name": name, "question": question})["id"]
        request(f"/api/catalog/projects/{project_id}/publish", {})

    public = request("/api/catalog/public")
    if not any(item["id"] == industry_id and item["name"] == INDUSTRY for item in public["industries"]):
        raise RuntimeError("Published industry readback mismatch")
    actual = {item["name"]: item["question"] for item in public["projects"]
              if item["industry_id"] == industry_id}
    if actual != dict(PROJECTS):
        raise RuntimeError(f"Published project readback mismatch: {actual}")
    print(json.dumps({"published": True, "industry_id": industry_id, "projects": actual}, ensure_ascii=False))


if __name__ == "__main__":
    main()
