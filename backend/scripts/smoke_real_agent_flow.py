from __future__ import annotations

import argparse
import asyncio
import codecs
import json
import time
from typing import Any
from urllib import error, request


THEMES = [
    ("大学生工作难找，投简历像进黑洞", "short"),
    ("上班内卷，会议从早排到晚", "medium"),
    ("考研考公焦虑，三条路都在排队", "minute"),
    ("租房压力，工资刚到账就被房租截胡", "short"),
    ("校门口卖烤肠也内卷，摊位费比利润先到", "medium"),
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--index", type=int, default=None, help="Run one 1-based test case index.")
    parser.add_argument("--parallel", action="store_true", help="Run selected cases concurrently.")
    parser.add_argument("--mode", choices=["agent", "workflow"], default="agent")
    args = parser.parse_args()

    if args.index:
        selected = [THEMES[max(0, min(len(THEMES) - 1, args.index - 1))]]
    else:
        selected = THEMES[: max(1, args.limit)]
    if args.parallel:
        results = await asyncio.gather(*[
            run_case(args.base_url.rstrip("/"), theme, duration_mode, args.mode)
            for theme, duration_mode in selected
        ])
    else:
        results = []
        for theme, duration_mode in selected:
            results.append(await run_case(args.base_url.rstrip("/"), theme, duration_mode, args.mode))
    print(json.dumps({"status": "ok", "cases": results}, ensure_ascii=False, indent=2))


async def run_case(base_url: str, theme: str, duration_mode: str, mode: str) -> dict[str, Any]:
    candidate_started = time.monotonic()
    candidate_events, candidate_final = await asyncio.to_thread(
        read_sse,
        f"{base_url}/api/maomeme/candidates-stream",
        {"theme": theme, "duration_mode": duration_mode, "use_doubao": mode == "agent", "generation_mode": mode},
        180,
    )
    candidate_elapsed = round(time.monotonic() - candidate_started, 2)
    candidates = candidate_final.get("candidates", []) if isinstance(candidate_final, dict) else []
    candidate = candidates[0] if candidates else {}

    select_started = time.monotonic()
    select_events, select_final = await asyncio.to_thread(
        read_sse,
        f"{base_url}/api/maomeme/select-stream",
        {
            "theme": theme,
            "duration_mode": duration_mode,
            "use_doubao": mode == "agent",
            "generation_mode": mode,
            "candidate": candidate,
        },
        260,
    )
    select_elapsed = round(time.monotonic() - select_started, 2)
    plan = select_final.get("plan", {}) if isinstance(select_final, dict) else {}
    timeline = plan.get("timeline", []) if isinstance(plan, dict) else []
    slot_events = [event for event in select_events if event.get("type") in {"slot", "slot_patch"}]
    first_slot_at = next((event.get("_elapsed") for event in slot_events if event.get("type") == "slot"), None)
    overlays = [
        action.get("text") or action.get("title") or action.get("object") or action.get("type")
        for slot in timeline
        for action in (slot.get("overlay_actions") or [])
        if isinstance(action, dict)
    ]
    motion_ids = [slot.get("motion", {}).get("id") for slot in timeline if isinstance(slot, dict)]
    background_text = " / ".join(str(slot.get("background", {}).get("description", "")) for slot in timeline[:3])
    return {
        "theme": theme,
        "duration_mode": duration_mode,
        "mode": mode,
        "candidate_elapsed_sec": candidate_elapsed,
        "candidate_count": len(candidates),
        "candidate_first_visible_sec": first_event_elapsed(candidate_events, {"agent_delta", "draft_candidate", "candidate"}),
        "selected_title": candidate.get("title") or candidate.get("name") or "",
        "select_elapsed_sec": select_elapsed,
        "first_slot_sec": first_slot_at,
        "timeline_slots": len(timeline),
        "slot_events": len(slot_events),
        "unique_motions": len(set(filter(None, motion_ids))),
        "overlay_count": len(overlays),
        "overlay_preview": overlays[:8],
        "background_preview": background_text[:240],
        "provider_notes": [
            note
            for note in plan.get("agent_notes", [])[:10]
            if isinstance(note, str) and ("生成来源" in note or "ShotPlannerAgent" in note or "Agent" in note)
        ],
    }


def first_event_elapsed(events: list[dict[str, Any]], event_type: str | set[str]) -> float | None:
    expected = {event_type} if isinstance(event_type, str) else event_type
    for event in events:
        if event.get("type") in expected:
            elapsed = event.get("_elapsed")
            return round(float(elapsed), 2) if elapsed is not None else None
    return None


def read_sse(url: str, payload: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.monotonic()
    req = request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    events: list[dict[str, Any]] = []
    final: dict[str, Any] = {}
    buffer = ""
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        response_context = request.urlopen(req, timeout=timeout)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"http_{exc.code}: {body}") from exc

    with response_context as response:
        while True:
            chunk = response.read(1024)
            if not chunk:
                break
            buffer += decoder.decode(chunk)
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                data_lines = [line[5:].strip() for line in frame.splitlines() if line.startswith("data:")]
                if not data_lines:
                    continue
                try:
                    event = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    continue
                event["_elapsed"] = round(time.monotonic() - started, 2)
                events.append(event)
                if event.get("type") in {"final", "done"}:
                    final = event
                if event.get("type") == "error":
                    raise RuntimeError(event.get("message", "stream error"))
        buffer += decoder.decode(b"", final=True)
    return events, final


if __name__ == "__main__":
    asyncio.run(main())
