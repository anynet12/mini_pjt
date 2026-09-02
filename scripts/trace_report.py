"""트레이스(JSONL) → 스팬·매트릭스 리포트 (패턴 11 · Observability).

`scripts/trace_hook.py` 가 쌓은 `traces/{date}/{session}.jsonl` 을 읽어 서브에이전트 스팬,
도구 호출 통계, 앵글×단계 매트릭스를 마크다운으로 출력한다.

사용법:
    python scripts/trace_report.py                     # 가장 최근 세션 파일
    python scripts/trace_report.py 2026-09-02          # 그 날짜의 모든 세션
    python scripts/trace_report.py traces/2026-09-02/abc.jsonl
    python scripts/trace_report.py --timeline          # 시간순 이벤트 목록도 함께
    python scripts/trace_report.py --json              # 리포트를 JSON 으로
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACE_ROOT = REPO_ROOT / "traces"
STAGES = ["researcher", "guide-researcher", "writer", "image", "finalizer", "reviewer", "poster"]


def load(paths):
    recs = []
    for p in paths:
        with Path(p).open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    recs.sort(key=lambda r: r.get("ts", ""))
    return recs


def resolve_paths(target: str | None) -> list[Path]:
    if target is None:
        files = sorted(TRACE_ROOT.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
        return files[-1:] if files else []
    p = Path(target)
    if p.is_file():
        return [p]
    day_dir = TRACE_ROOT / target
    if day_dir.is_dir():
        return sorted(day_dir.glob("*.jsonl"))
    return []


def ts(r):
    try:
        return datetime.fromisoformat(r["ts"])
    except (KeyError, ValueError):
        return None


def fmt_dur(seconds):
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def analyze(recs):
    # --- 서브에이전트 스팬 (SubagentStart → SubagentStop, agent_id 로 짝 맞춤)
    spans = {}
    for r in recs:
        aid = r.get("agent_id")
        if r["event"] == "SubagentStart" and aid:
            spans[aid] = {"agent_id": aid, "agent_type": r.get("agent_type"), "start": ts(r), "end": None,
                          "tools": Counter(), "angle": Counter(), "run": Counter(), "verdict": None, "total": None}
        elif r["event"] == "SubagentStop" and aid:
            sp = spans.setdefault(aid, {"agent_id": aid, "agent_type": r.get("agent_type"), "start": None, "end": None,
                                        "tools": Counter(), "angle": Counter(), "run": Counter(), "verdict": None, "total": None})
            sp["end"] = ts(r)
            sp["verdict"] = r.get("verdict")
            sp["total"] = r.get("total")
            if r.get("angle"):
                sp["angle"][r["angle"]] += 1
        elif r["event"] == "PreToolUse" and aid in spans:
            spans[aid]["tools"][r.get("tool", "?")] += 1
            if r.get("angle"):
                spans[aid]["angle"][r["angle"]] += 1
            if r.get("run"):
                spans[aid]["run"][r["run"]] += 1
    for sp in spans.values():
        sp["duration"] = (sp["end"] - sp["start"]).total_seconds() if sp["start"] and sp["end"] else None
        sp["angle_main"] = sp["angle"].most_common(1)[0][0] if sp["angle"] else None
        sp["run_main"] = sp["run"].most_common(1)[0][0] if sp["run"] else None

    # --- 도구 호출 (PreToolUse ↔ PostToolUse/Failure, tool_use_id 로 짝 맞춤)
    pending = {}
    tool_stats = defaultdict(lambda: {"count": 0, "fail": 0, "durs": []})
    for r in recs:
        tid = r.get("tool_use_id")
        tool = r.get("tool", "?")
        if r["event"] == "PreToolUse":
            tool_stats[tool]["count"] += 1
            if tid:
                pending[tid] = ts(r)
        elif r["event"] in ("PostToolUse", "PostToolUseFailure"):
            if r["event"] == "PostToolUseFailure":
                tool_stats[tool]["fail"] += 1
            t0 = pending.pop(tid, None) if tid else None
            t1 = ts(r)
            if t0 and t1:
                tool_stats[tool]["durs"].append((t1 - t0).total_seconds())

    # --- 앵글 × 단계 매트릭스 (서브에이전트 스팬 기준: 시도 횟수, 누적 시간, reviewer 판정)
    matrix = defaultdict(lambda: defaultdict(lambda: {"attempts": 0, "seconds": 0.0, "verdicts": []}))
    for sp in spans.values():
        stage = sp["agent_type"] or "?"
        angle = sp["angle_main"] or "(공유)"
        cell = matrix[angle][stage]
        cell["attempts"] += 1
        cell["seconds"] += sp["duration"] or 0.0
        if sp["verdict"]:
            cell["verdicts"].append(f"{sp['verdict']}{'(' + str(sp['total']) + ')' if sp['total'] is not None else ''}")

    first, last = ts(recs[0]) if recs else None, ts(recs[-1]) if recs else None
    return {
        "events": len(recs),
        "sessions": sorted({r.get("session") for r in recs if r.get("session")}),
        "span_seconds": (last - first).total_seconds() if first and last else None,
        "first_ts": recs[0].get("ts") if recs else None,
        "last_ts": recs[-1].get("ts") if recs else None,
        "prompts": sum(1 for r in recs if r["event"] == "UserPromptSubmit"),
        "spans": sorted(spans.values(), key=lambda s: (s["start"] is None, s["start"].isoformat() if s["start"] else "")),
        "tools": dict(tool_stats),
        "matrix": matrix,
    }


def render_md(a, recs, timeline=False) -> str:
    L = []
    L.append("# 트레이스 리포트")
    L.append("")
    L.append(f"- 세션: {', '.join(s[:8] for s in a['sessions']) or '-'}")
    L.append(f"- 이벤트 수: {a['events']} · 사용자 프롬프트: {a['prompts']} · 서브에이전트 스팬: {len(a['spans'])}")
    L.append(f"- 구간: {a['first_ts']} → {a['last_ts']} ({fmt_dur(a['span_seconds'])})")
    L.append("")
    L.append("## 서브에이전트 스팬")
    L.append("")
    L.append("| # | 에이전트 | 앵글 | 시작 | 소요 | 도구 호출 | 판정 |")
    L.append("|---|---|---|---|---|---|---|")
    for i, sp in enumerate(a["spans"], 1):
        tools = ", ".join(f"{k}×{v}" for k, v in sp["tools"].most_common()) or "-"
        start = sp["start"].strftime("%H:%M:%S") if sp["start"] else "-"
        verdict = f"{sp['verdict']} {sp['total'] if sp['total'] is not None else ''}".strip() if sp["verdict"] else ""
        L.append(f"| {i} | {sp['agent_type'] or '?'} | {sp['angle_main'] or '-'} | {start} | {fmt_dur(sp['duration'])} | {tools} | {verdict} |")
    L.append("")
    L.append("## 앵글 × 단계 매트릭스 (시도횟수 · 누적시간 · reviewer 판정)")
    L.append("")
    stages = [s for s in STAGES if any(s in row for row in a["matrix"].values())]
    extra = sorted({s for row in a["matrix"].values() for s in row} - set(stages))
    stages += extra
    L.append("| 앵글 | " + " | ".join(stages) + " |")
    L.append("|---|" + "---|" * len(stages))
    for angle in sorted(a["matrix"]):
        cells = []
        for s in stages:
            c = a["matrix"][angle].get(s)
            if not c:
                cells.append("·")
                continue
            txt = f"{c['attempts']}회 {fmt_dur(c['seconds'])}"
            if c["verdicts"]:
                txt += " " + "→".join(c["verdicts"])
            cells.append(txt)
        L.append(f"| {angle} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("## 도구 호출 통계")
    L.append("")
    L.append("| 도구 | 호출 | 실패 | 평균 | 최대 |")
    L.append("|---|---|---|---|---|")
    for tool, st in sorted(a["tools"].items(), key=lambda kv: -kv[1]["count"]):
        durs = st["durs"]
        avg = sum(durs) / len(durs) if durs else None
        mx = max(durs) if durs else None
        L.append(f"| {tool} | {st['count']} | {st['fail']} | {fmt_dur(avg)} | {fmt_dur(mx)} |")
    if timeline:
        L.append("")
        L.append("## 타임라인")
        L.append("")
        for r in recs:
            t = r.get("ts", "")[11:23]
            who = r.get("agent_type") or "main"
            what = r.get("tool") or r.get("startup_type") or r.get("end_reason") or ""
            detail = r.get("summary") or r.get("prompt") or r.get("message") or r.get("error") or ""
            if isinstance(detail, dict):
                detail = " ".join(f"{k}={v}" for k, v in detail.items())
            L.append(f"- `{t}` {r['event']:<18} {who:<16} {what:<14} {str(detail)[:120]}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="jsonl 파일 경로 또는 날짜(YYYY-MM-DD). 생략 시 최근 세션")
    ap.add_argument("--timeline", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths = resolve_paths(args.target)
    if not paths:
        print(f"트레이스 파일을 찾을 수 없습니다: {args.target or TRACE_ROOT}", file=sys.stderr)
        sys.exit(1)
    recs = load(paths)
    if not recs:
        print("레코드가 없습니다.", file=sys.stderr)
        sys.exit(1)
    a = analyze(recs)
    if args.json:
        def default(o):
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, (Counter, defaultdict)):
                return dict(o)
            return str(o)
        print(json.dumps(a, ensure_ascii=False, indent=2, default=default))
    else:
        print(render_md(a, recs, timeline=args.timeline))


if __name__ == "__main__":
    main()
