"""초안 faithfulness 검사 CLI (패턴 12 정량 지표 · 패턴 3 재사용).

    python scripts/check_faithfulness.py posts/{date}/g{N}/draft_vN.md --run {date} --angle g{N} [--kind source|transcript]
                                         [--top-k 3] [--max-claims 20] [--json]

초안의 사실 주장을 뽑아 색인된 근거(자막·공식 페이지·research.md)와 대조하고, 같은 폴더에
`faithfulness_vN.json` 을 쓴다. stdout 에는 요약 + unsupported 주장 목록을 찍는다.
오케스트레이터는 finalizer 완료 후 reviewer 호출 전에 실행하고, 결과 경로를 reviewer 입력에 넘긴다.

--kind 를 생략하면 run/angle 필터 안의 모든 종류(자막·research·source)를 근거로 본다.
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.rag.faithfulness import check_faithfulness  # noqa: E402
from backend.rag.retriever import HybridRetriever  # noqa: E402

VERSION_RE = re.compile(r"_v(\d+)\.(md|html)$")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft", help="draft_vN.md (또는 post_vN.html)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--angle", default=None, help="a{N}/g{N}. 영상 트랙에서 자막(공유 자료)만 대조하려면 생략")
    ap.add_argument("--kind", default=None)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--max-claims", type=int, default=20)
    ap.add_argument("--rerank", action="store_true", help="근거 검색에 리랭킹 사용 (Bedrock Rerank 권한이 있을 때만 권장)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = Path(args.draft)
    if not path.is_file():
        print(f"파일 없음: {path}", file=sys.stderr)
        sys.exit(1)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".html":
        text = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)

    try:
        retriever = HybridRetriever(expand=False, rerank=args.rerank)
        report = check_faithfulness(text, draft_name=path.name, run=args.run, angle=args.angle, kind=args.kind,
                                    retriever=retriever, top_k=args.top_k, max_claims=args.max_claims)
    except Exception as e:  # noqa: BLE001
        print(f"검사 실패: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)

    m = VERSION_RE.search(path.name)
    out = path.parent / f"faithfulness_v{m.group(1) if m else '0'}.json"
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    if args.json:
        print(report.model_dump_json(indent=2))
        return
    print(f"# faithfulness {report.faithfulness:.2f}  (주장 {report.n_claims}개: supported {report.supported} · "
          f"partially {report.partially} · unsupported {report.unsupported} · 리랭킹={report.rerank_mode or '없음'})")
    print(f"저장: {out}")
    bad = [i for i in report.items if i["verdict"] != "supported"]
    if bad:
        print("\n## 근거가 약하거나 없는 주장 (writer 재작업 대상)")
        for i in bad:
            print(f"- [{i['verdict']}] {i['claim']}" + (f"  — {i['note']}" if i["note"] else "")
                  + (f"  (근거: {i['evidence_ref']})" if i["evidence_ref"] and i["verdict"] == "partially" else ""))


if __name__ == "__main__":
    main()
