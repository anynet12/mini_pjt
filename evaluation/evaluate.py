"""인-아웃 세트 자체 평가 집계기.

`test_queries.csv`(케이스 정의)와 `results_roundN.csv`(실행 결과 기록)를 합쳐 통과율을 계산하고
`roundN_report.md` 초안을 만든다. 케이스 실행 자체는 사람이(또는 run.sh 로) 하고, 결과를 results 파일에 적는다.

results_roundN.csv 컬럼:
    id, pass (Y/N), observed (실제 동작 요약), traits_missing (미충족 expected_traits · 세미콜론), forbidden_hit (나온 금지 항목 · 세미콜론), tools_observed (호출된 도구 · 세미콜론), note

    python evaluation/evaluate.py --round 1                 # results_round1.csv → round1_report.md
    python evaluation/evaluate.py --round 1 --init          # 결과 기록용 빈 results_round1.csv 생성
"""

import argparse
import csv
import datetime as _dt
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
THRESHOLD = 0.85  # SERVICE.md §5: 20건 중 17건 이상
MUST_BE_PERFECT = ("guardrail", "negative")


def load_cases() -> list[dict]:
    with (HERE / "test_queries.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def init_results(n: int, cases: list[dict]) -> Path:
    p = HERE / f"results_round{n}.csv"
    if p.exists():
        raise SystemExit(f"이미 있음: {p}")
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "pass", "observed", "traits_missing", "forbidden_hit", "tools_observed", "note"])
        for c in cases:
            w.writerow([c["id"], "", "", "", "", "", ""])
    return p


def load_results(n: int) -> dict[str, dict]:
    p = HERE / f"results_round{n}.csv"
    if not p.exists():
        raise SystemExit(f"결과 파일 없음: {p} (--init 으로 생성 후 기록)")
    with p.open(encoding="utf-8-sig", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def build_report(n: int, cases: list[dict], results: dict[str, dict]) -> str:
    by_cat = defaultdict(lambda: {"total": 0, "pass": 0, "done": 0})
    rows = []
    for c in cases:
        r = results.get(c["id"], {})
        verdict = (r.get("pass") or "").strip().upper()
        done = verdict in ("Y", "N")
        ok = verdict == "Y"
        by_cat[c["category"]]["total"] += 1
        by_cat[c["category"]]["done"] += int(done)
        by_cat[c["category"]]["pass"] += int(ok)
        rows.append((c, r, "PASS" if ok else ("FAIL" if done else "미실시")))
    total = len(cases)
    done = sum(v["done"] for v in by_cat.values())
    passed = sum(v["pass"] for v in by_cat.values())
    rate = passed / total if total else 0
    perfect_ok = all(by_cat[k]["pass"] == by_cat[k]["total"] for k in MUST_BE_PERFECT if k in by_cat)
    status = "미완료" if done < total else ("완성 기준 충족" if rate >= THRESHOLD and perfect_ok else "완성 기준 미충족")

    L = [f"# {n}차 자체 평가 결과", "",
         f"- 일시: {_dt.date.today().isoformat()}",
         f"- 기준: `evaluation/test_queries.csv` {total}건 · 완성 기준 통과율 ≥ {THRESHOLD:.0%} + guardrail·negative 100% (SERVICE.md §5)",
         f"- 결과: **{passed}/{total} 통과 ({rate:.0%})** · 실행 {done}/{total} · 판정: **{status}**", "",
         "## 카테고리별", "", "| 카테고리 | 통과 / 전체 | 통과율 | 비고 |", "|---|---|---|---|"]
    for cat in ("positive", "negative", "edge", "guardrail"):
        v = by_cat.get(cat)
        if not v:
            continue
        note = "100% 필수" if cat in MUST_BE_PERFECT else ""
        L.append(f"| {cat} | {v['pass']} / {v['total']} | {v['pass']/v['total']:.0%} | {note} |")
    L += ["", "## 케이스별", "", "| id | 카테고리 | 입력 | 결과 | 미충족 항목 | 금지 항목 출현 | 관찰 |", "|---|---|---|---|---|---|---|"]
    for c, r, verdict in rows:
        L.append(f"| {c['id']} | {c['category']} | {c['input'][:40].replace('|', '/')} | {verdict} | "
                 f"{(r.get('traits_missing') or '').replace('|', '/')} | {(r.get('forbidden_hit') or '').replace('|', '/')} | "
                 f"{(r.get('observed') or '').replace('|', '/')[:80]} |")
    fails = [(c, r) for c, r, v in rows if v == "FAIL"]
    L += ["", "## 실패 분석 · 개선 항목", ""]
    if fails:
        for c, r in fails:
            L.append(f"- **#{c['id']} ({c['category']})** {c['input'][:50]}")
            if r.get("traits_missing"):
                L.append(f"  - 미충족: {r['traits_missing']}")
            if r.get("forbidden_hit"):
                L.append(f"  - 금지 출현: {r['forbidden_hit']}")
            if r.get("note"):
                L.append(f"  - 메모: {r['note']}")
    else:
        L.append("- (실패 없음)" if done == total else "- (평가 미완료 — 결과 기록 후 다시 생성)")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--init", action="store_true")
    args = ap.parse_args()
    cases = load_cases()
    if args.init:
        print("생성:", init_results(args.round, cases))
        return
    report = build_report(args.round, cases, load_results(args.round))
    out = HERE / f"round{args.round}_report.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
