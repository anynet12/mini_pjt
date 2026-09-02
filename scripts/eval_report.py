"""품질 게이트 정량 리포트 (패턴 12 · 평가/LLM-as-Judge 집계).

`scripts/parse_review.py` 가 앵글 폴더마다 쌓은 `review_log.jsonl` 을 모아 실행(또는 전체) 단위의
정량 지표를 마크다운으로 낸다. 워크플로우 6단계 "로그 분석 보고" 의 근거 자료.

    python scripts/eval_report.py posts/2026-09-02        # 한 실행
    python scripts/eval_report.py --all                    # posts/ 전체 (추세)
    python scripts/eval_report.py posts/2026-09-02 --json

지표: 앵글별 판정 이력, 최초 심사 PASS율, 최종 PASS율, drop 수, 평균 재작업 횟수, 기준별(A/B/C/D)
평균 점수, 원인 단계·기준별 지적 빈도, 반복 지적 상위 항목, 정합성 경고.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS = REPO_ROOT / "posts"
MAX_REWORK = 2  # CLAUDE.md 품질 게이트: 재작업 최대 2회, 3번째 FAIL → drop


def load_logs(root: Path) -> dict[str, list[dict]]:
    by_angle: dict[str, list[dict]] = defaultdict(list)
    for f in sorted(root.rglob("review_log.jsonl")):
        key = f"{f.parent.parent.name}/{f.parent.name}"
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        by_angle[key].append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        by_angle[key].sort(key=lambda r: (r.get("version") or 0, r.get("reviewed_at", "")))
    return by_angle


def analyze(by_angle: dict[str, list[dict]]) -> dict:
    angles = []
    crit_scores = defaultdict(list)
    issue_by_stage = Counter()
    issue_by_crit = Counter()
    issue_by_sev = Counter()
    repeated = Counter()
    warnings = []
    for key, recs in by_angle.items():
        verdicts = [r["result"]["verdict"] for r in recs]
        final = verdicts[-1] if verdicts else None
        fails = verdicts.count("FAIL")
        dropped = final == "FAIL" and fails > MAX_REWORK
        angles.append({
            "angle": key,
            "reviews": len(recs),
            "history": verdicts,
            "totals": [r["result"]["scores"]["total"] for r in recs],
            "first_pass": verdicts[0] == "PASS" if verdicts else None,
            "final_pass": final == "PASS",
            "rework": min(fails, MAX_REWORK) if final == "PASS" else fails,
            "dropped": dropped,
        })
        for r in recs:
            s = r["result"]["scores"]
            for k, f in (("A", "a_format"), ("B", "b_appeal"), ("C", "c_coherence"), ("D", "d_traces"), ("종합", "total")):
                crit_scores[k].append(s[f])
            for it in r["result"].get("issues", []):
                if it["severity"] == "minor":
                    continue
                issue_by_stage[it["stage"]] += 1
                issue_by_crit[it["criterion"]] += 1
                issue_by_sev[it["severity"]] += 1
                repeated[(it["criterion"], it["stage"], it["problem"][:60])] += 1
            for w in r.get("consistency_warnings", []):
                warnings.append(f"{key} v{r.get('version')}: {w}")
    n = len(angles)
    return {
        "angles": angles,
        "n_angles": n,
        "n_reviews": sum(a["reviews"] for a in angles),
        "first_pass_rate": (sum(1 for a in angles if a["first_pass"]) / n) if n else None,
        "final_pass_rate": (sum(1 for a in angles if a["final_pass"]) / n) if n else None,
        "dropped": sum(1 for a in angles if a["dropped"]),
        "avg_rework": (sum(a["rework"] for a in angles) / n) if n else None,
        "avg_scores": {k: round(sum(v) / len(v), 1) for k, v in crit_scores.items() if v},
        "issues_by_stage": dict(issue_by_stage),
        "issues_by_criterion": dict(issue_by_crit),
        "issues_by_severity": dict(issue_by_sev),
        "repeated": [{"criterion": c, "stage": s, "problem": p, "count": k} for (c, s, p), k in repeated.most_common(10) if k >= 1],
        "warnings": warnings,
    }


def pct(x):
    return "-" if x is None else f"{x * 100:.0f}%"


def render_md(a: dict, title: str) -> str:
    L = [f"# 품질 게이트 리포트 — {title}", ""]
    L.append(f"- 앵글 {a['n_angles']}개 · 심사 {a['n_reviews']}회 · 최초 PASS율 {pct(a['first_pass_rate'])} · "
             f"최종 PASS율 {pct(a['final_pass_rate'])} · drop {a['dropped']}개 · 평균 재작업 "
             f"{'-' if a['avg_rework'] is None else f'{a['avg_rework']:.1f}'}회")
    if a["avg_scores"]:
        L.append("- 평균 점수: " + " · ".join(f"{k} {v}" for k, v in a["avg_scores"].items()))
    L += ["", "## 앵글별 판정 이력", "", "| 앵글 | 심사 | 이력 | 점수 추이 | 최종 |", "|---|---|---|---|---|"]
    for x in a["angles"]:
        final = "DROP" if x["dropped"] else ("PASS" if x["final_pass"] else "FAIL(진행중)")
        L.append(f"| {x['angle']} | {x['reviews']} | {' → '.join(x['history'])} | {' → '.join(map(str, x['totals']))} | {final} |")
    L += ["", "## 지적 분포 (minor 제외)", ""]
    L.append("- 원인 단계: " + (" · ".join(f"{k} {v}" for k, v in sorted(a["issues_by_stage"].items(), key=lambda kv: -kv[1])) or "없음"))
    L.append("- 기준: " + (" · ".join(f"{k} {v}" for k, v in sorted(a["issues_by_criterion"].items())) or "없음"))
    L.append("- 심각도: " + (" · ".join(f"{k} {v}" for k, v in a["issues_by_severity"].items()) or "없음"))
    if a["repeated"]:
        L += ["", "## 반복 지적 상위", ""]
        for r in a["repeated"]:
            L.append(f"- ×{r['count']} [{r['criterion']}/{r['stage']}] {r['problem']}")
    if a["warnings"]:
        L += ["", "## 정합성 경고 (판정 규칙과 어긋난 심사)", ""]
        L += [f"- {w}" for w in a["warnings"]]
    L += ["", "## 개선 포인트 후보 (자동 제안 — 반영 여부는 사용자 결정)", ""]
    suggestions = []
    for stage, cnt in sorted(a["issues_by_stage"].items(), key=lambda kv: -kv[1])[:2]:
        if cnt >= 2:
            suggestions.append(f"`{stage}` 단계 지적이 {cnt}건 — `.claude/agents/{stage}.md` 규칙 보강 검토")
    for r in a["repeated"][:3]:
        if r["count"] >= 2:
            suggestions.append(f"같은 지적이 {r['count']}회 반복: \"{r['problem']}\" → 해당 단계 지침에 명시적 금지/체크 추가")
    L += [f"- {s}" for s in suggestions] or ["- 반복 패턴 없음"]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", nargs="?", help="posts/{date} 폴더")
    ap.add_argument("--all", action="store_true", help="posts/ 전체")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.all:
        root, title = POSTS, "posts/ 전체"
    elif args.run_dir:
        root, title = Path(args.run_dir).resolve(), Path(args.run_dir).name
    else:
        ap.error("run_dir 또는 --all 을 지정하세요")
    if not root.is_dir():
        print(f"폴더 없음: {root}", file=sys.stderr)
        sys.exit(1)
    by_angle = load_logs(root)
    if not by_angle:
        print(f"review_log.jsonl 이 없습니다: {root}", file=sys.stderr)
        sys.exit(1)
    a = analyze(by_angle)
    print(json.dumps(a, ensure_ascii=False, indent=2) if args.json else render_md(a, title))


if __name__ == "__main__":
    main()
