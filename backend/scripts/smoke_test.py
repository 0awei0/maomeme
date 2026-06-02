from __future__ import annotations

import json
import time
import urllib.request


BASE = "http://localhost:8000"
THEME = "大学生工作难找，投简历像进黑洞，岗位要求越来越离谱"


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    candidates = post("/api/maomeme/candidates", {"theme": THEME, "use_doubao": False})["candidates"]
    assert len(candidates) == 3, f"expected 3 candidates, got {len(candidates)}"

    plan = post("/api/maomeme/select", {"theme": THEME, "candidate": candidates[0], "use_doubao": False})["plan"]
    assert plan["timeline"], "plan timeline is empty"

    revised = post(
        "/api/maomeme/revise",
        {"theme": THEME, "instruction": "更讽刺一点，但结尾温暖一点", "plan": plan, "use_doubao": False},
    )["plan"]
    assert revised["timeline"][-1]["copy"], "revised plan missing ending copy"

    job = post("/api/maomeme/render-jobs", {"plan": revised, "packaging_engine": "auto", "allow_ai_fill": False})["job"]
    job_id = job["job_id"]
    for _ in range(90):
        current = get(f"/api/maomeme/render-jobs/{job_id}")["job"]
        if current["status"] in {"done", "error"}:
            break
        time.sleep(1)
    assert current["status"] == "done", current
    assert current["output_path"], current
    print(json.dumps({"status": "ok", "job": current}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
