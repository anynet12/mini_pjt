"""reviewer 판정문(review_vN.md) → 구조화 JSON + 판정 로그 누적 (패턴 1 · 12).

오케스트레이터가 reviewer 서브에이전트의 판정 전문을 `posts/{date}/a{N}/review_vN.md` 로 저장한
직후 실행한다 (PASS/FAIL 모두). N 은 심사 대상 `post_vN.html` 의 번호와 맞춘다.

    python scripts/parse_review.py posts/{date}/a{N}/review_vN.md

산출물 (같은 폴더):
    review_vN.json     ReviewRecord — verdict, scores(A/B/C/D/total), issues[{criterion,severity,stage,...}]
    review_log.jsonl   판정 1건 = 1줄 누적 (eval_report.py 가 읽음)
    review_log.md      사람이 읽는 누적 로그 (PASS 는 요약, FAIL 은 지시 목록 + 전문)

stdout 에는 오케스트레이터가 바로 쓸 한 줄 요약 JSON 을 찍는다:
    {"verdict":"FAIL","total":62,"rework_stages":["writer","finalizer"],"warnings":[...],"json":"..."}
옵션: --no-log (로그 누적 생략), --dry-run (파일 저장 없이 stdout 만)
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.chains.review_chain import ReviewRecord, parse_review_text  # noqa: E402

VERSION_RE = re.compile(r"review_v(\d+)\.md$")


def rel(path: Path) -> str:
    """저장소 안이면 상대 경로, 밖이면 절대 경로 문자열."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def infer_meta(path: Path) -> dict:
    m = VERSION_RE.search(path.name)
    angle_dir = path.parent
    return {
        "version": int(m.group(1)) if m else None,
        "angle": angle_dir.name if re.fullmatch(r"[ag]\d+", angle_dir.name) else None,
        "run": angle_dir.parent.name if angle_dir.parent.name[:4].isdigit() else None,
        "source_file": rel(path),
    }


def md_block(rec: ReviewRecord, review_text: str) -> str:
    r = rec.result
    s = r.scores
    lines = [
        f"## [{rec.reviewed_at}] post_v{rec.version} → **{r.verdict}** (종합 {s.total}/100)",
        "",
        f"- 기준별: A 양식 {s.a_format} · B 흡인력 {s.b_appeal} · C 문맥 {s.c_coherence} · D 흔적 {s.d_traces}",
    ]
    if r.strengths:
        lines.append(f"- 강점: {' / '.join(r.strengths)}")
    if r.weaknesses:
        lines.append(f"- 약점: {' / '.join(r.weaknesses)}")
    if r.issues:
        lines.append("- 지적:")
        for i, it in enumerate(r.issues, 1):
            lines.append(f"  {i}. [{it.criterion}/{it.severity}/{it.stage}] {it.problem} → {it.instruction}")
    if rec.consistency_warnings:
        lines.append(f"- ⚠ 정합성 경고: {' / '.join(rec.consistency_warnings)}")
    if rec.adjustments:
        lines.append(f"- 보정: {' / '.join(rec.adjustments)}")
    if r.verdict == "FAIL":
        lines += ["", "<details><summary>판정 전문</summary>", "", review_text.strip(), "", "</details>"]
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review_file", help="posts/{date}/a{N}/review_vN.md")
    ap.add_argument("--no-log", action="store_true", help="review_log.md/.jsonl 누적 생략")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 결과만 출력")
    args = ap.parse_args()

    path = Path(args.review_file).resolve()
    if not path.is_file():
        print(f"파일 없음: {path}", file=sys.stderr)
        sys.exit(1)
    review_text = path.read_text(encoding="utf-8")
    meta = infer_meta(path)

    try:
        rec = parse_review_text(review_text, **meta)
    except Exception as e:  # noqa: BLE001
        print(f"구조화 실패: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)

    json_path = path.with_suffix(".json")
    if not args.dry_run:
        json_path.write_text(rec.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
        if not args.no_log:
            angle_dir = path.parent
            with (angle_dir / "review_log.jsonl").open("a", encoding="utf-8") as f:
                f.write(rec.model_dump_json(exclude_none=True) + "\n")
            log_md = angle_dir / "review_log.md"
            header = "" if log_md.exists() else f"# 판정 로그 — {meta.get('run')}/{meta.get('angle')}\n\n"
            with log_md.open("a", encoding="utf-8") as f:
                f.write(header + md_block(rec, review_text))

    out = {
        "verdict": rec.result.verdict,
        "total": rec.result.scores.total,
        "rework_stages": rec.rework_stages,
        "issues": len(rec.result.issues),
        "warnings": rec.consistency_warnings,
        "adjustments": rec.adjustments,
        "json": None if args.dry_run else rel(json_path),
    }
    if args.dry_run:
        out["record"] = json.loads(rec.model_dump_json(exclude_none=True))
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.dry_run else None))


if __name__ == "__main__":
    main()
