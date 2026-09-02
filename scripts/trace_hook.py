"""Claude Code hook → JSONL 트레이스 기록 (패턴 11 · Observability).

`.claude/settings.json` 의 hooks 가 세션·서브에이전트·도구 이벤트마다 이 스크립트를 실행한다.
stdin 으로 받은 hook JSON 을 한 줄로 압축해 `traces/{YYYY-MM-DD}/{session_id}.jsonl` 에 append 한다.

- 어떤 경우에도 exit 0, stdout 출력 없음 (PreToolUse 는 stdout 을 결정 JSON 으로 해석하므로).
- 파이프라인 진행에는 영향을 주지 않는다. 분석·리포트는 `scripts/trace_report.py` 가 한다.
- 각 레코드는 실행 폴더(run=posts/{date}), 앵글(a{N}/g{N}), 단계(stage)를 tool_input 경로에서
  추론해 붙인다 → 앵글×단계 매트릭스와 스팬 duration 을 나중에 재구성할 수 있다.

수동 테스트:  echo '{"hook_event_name":"Stop","session_id":"t"}' | python scripts/trace_hook.py
"""

import datetime as _dt
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACE_ROOT = REPO_ROOT / "traces"

RUN_RE = re.compile(r"posts[\\/]+(\d{4}-\d{2}-\d{2}(?:-\d+)?)(?:[\\/]+([ag]\d+))?")
STAGE_BY_PATH = [
    (re.compile(r"transcript\.txt|insights\.md|[\\/]subs[\\/]"), "researcher"),
    (re.compile(r"keyword_candidates\.md|research\.md"), "guide-researcher"),
    (re.compile(r"draft_v\d+\.md"), "writer"),
    (re.compile(r"images_v\d+[\\/]|svg_to_png\.py"), "image"),
    (re.compile(r"post_v\d+\.html|post_tistory\.html|post_naver\.html"), "finalizer"),
    (re.compile(r"review_v\d+\.(?:md|json)|review_log|parse_review\.py"), "reviewer"),
    (re.compile(r"tistory_post\.py"), "poster"),
]
VERDICT_RE = re.compile(r"##\s*판정\s*[:：]\s*(PASS|FAIL)")
TOTAL_RE = re.compile(r"종합\s*[:：]?\s*(\d{1,3})\s*/\s*100")
CLIP = 300


def clip(value, n=CLIP):
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = s.replace("\r", "").replace("\n", " ⏎ ")
    return s if len(s) <= n else s[: n - 1] + "…"


def summarize_tool(tool_name: str, tool_input) -> dict:
    """도구 입력을 짧은 요약으로 줄인다 (전문은 트랜스크립트에 있으니 여기선 식별 정보만)."""
    if not isinstance(tool_input, dict):
        return {"input": clip(tool_input)}
    out = {}
    if tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        out["file"] = tool_input.get("file_path") or tool_input.get("notebook_path")
        if tool_name == "Write" and isinstance(tool_input.get("content"), str):
            out["bytes"] = len(tool_input["content"].encode("utf-8"))
    elif tool_name in ("Bash", "PowerShell"):
        out["command"] = clip(tool_input.get("command", ""), 200)
    elif tool_name in ("Agent", "Task"):
        out["subagent_type"] = tool_input.get("subagent_type")
        out["description"] = clip(tool_input.get("description", ""), 120)
    elif tool_name == "AskUserQuestion":
        qs = tool_input.get("questions") or []
        out["questions"] = len(qs)
        if qs and isinstance(qs[0], dict):
            out["options"] = len(qs[0].get("options") or [])
            out["multiSelect"] = qs[0].get("multiSelect")
    elif tool_name in ("WebSearch", "WebFetch", "Grep", "Glob"):
        out["query"] = clip(tool_input.get("query") or tool_input.get("url") or tool_input.get("pattern") or "", 160)
    else:
        out["input"] = clip(tool_input, 160)
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def infer_context(blob: str) -> dict:
    ctx = {}
    m = RUN_RE.search(blob)
    if m:
        ctx["run"] = m.group(1)
        if m.group(2):
            ctx["angle"] = m.group(2)
    for pat, stage in STAGE_BY_PATH:
        if pat.search(blob):
            ctx["stage"] = stage
            break
    return ctx


def build_record(ev: dict) -> dict:
    event = ev.get("hook_event_name", "?")
    rec = {
        "ts": _dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "event": event,
        "session": ev.get("session_id"),
        "prompt_id": ev.get("prompt_id"),
        "agent_id": ev.get("agent_id"),
        "agent_type": ev.get("agent_type"),
    }
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        tool = ev.get("tool_name", "?")
        rec["tool"] = tool
        rec["tool_use_id"] = ev.get("tool_use_id")
        tool_input = ev.get("tool_input")
        rec["summary"] = summarize_tool(tool, tool_input)
        ctx = infer_context(json.dumps(tool_input, ensure_ascii=False) if tool_input is not None else "")
        if ev.get("agent_type"):
            # 서브에이전트 안의 도구 호출: 단계 = 그 서브에이전트. 경로 기반 추론은 메인 에이전트 호출에만 쓴다
            # (writer 가 transcript.txt 를 읽는다고 researcher 단계가 아니다)
            ctx["stage"] = ev["agent_type"]
        rec.update(ctx)
        if event == "PostToolUse":
            resp = ev.get("tool_response")
            rec["response_chars"] = len(json.dumps(resp, ensure_ascii=False)) if resp is not None else 0
        if event == "PostToolUseFailure":
            rec["error"] = clip(ev.get("error_message", ""), 200)
    elif event in ("SubagentStart", "SubagentStop"):
        rec["stage"] = ev.get("agent_type")
        if event == "SubagentStop":
            msg = ev.get("last_assistant_message") or ""
            rec["message"] = clip(msg, 240)
            if ev.get("agent_type") == "reviewer":
                mv = VERDICT_RE.search(msg)
                mt = TOTAL_RE.search(msg)
                if mv:
                    rec["verdict"] = mv.group(1)
                if mt:
                    rec["total"] = int(mt.group(1))
            rec.update({k: v for k, v in infer_context(msg).items() if k != "stage"})
    elif event == "SessionStart":
        rec["startup_type"] = ev.get("startup_type")
        rec["model"] = ev.get("model")
    elif event == "SessionEnd":
        rec["end_reason"] = ev.get("end_reason")
    elif event == "UserPromptSubmit":
        rec["prompt"] = clip(ev.get("prompt", ""), 200)
    elif event == "Stop":
        rec["message"] = clip(ev.get("last_assistant_message") or "", 200)
    return {k: v for k, v in rec.items() if v is not None}


def main() -> None:
    try:
        raw = sys.stdin.read()
        ev = json.loads(raw) if raw.strip() else {}
        rec = build_record(ev)
        day = _dt.date.today().isoformat()
        session = (ev.get("session_id") or "unknown")[:36]
        out_dir = TRACE_ROOT / day
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / f"{session}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — hook 은 절대 파이프라인을 깨면 안 된다
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
