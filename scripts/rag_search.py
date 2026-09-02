"""하이브리드 RAG 검색 CLI (패턴 3): 쿼리 확장 → BM25 + 밀집 → RRF → 리랭킹 → top-k.

    python scripts/rag_search.py "연금저축 이체 수수료" --run {date} [--angle g1] [--kind source] [-k 6]
                                 [--no-expand] [--no-rerank] [--json] [--verbose]

writer / guide-researcher 가 근거 청크를 찾을 때 쓴다. 출력은 청크 본문 + 출처 참조(파일#청크, URL) 라서
그대로 인용·귀속에 쓸 수 있다. 결과 밖의 사실을 지어내지 않는다는 원칙은 그대로다.

필터 조합 예:
    --run 2026-09-02 --kind transcript          영상 트랙: 이 실행의 자막만
    --run 2026-09-02 --angle g1 --kind source   가이드 트랙: 이 키워드의 공식 페이지 원문만
    --kind post                                 과거 발행글 전체 (키워드 중복 회피)
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.rag.retriever import HybridRetriever  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query")
    ap.add_argument("--run", default=None)
    ap.add_argument("--angle", default=None)
    ap.add_argument("--kind", default=None)
    ap.add_argument("--source", default=None, help="특정 파일(source 경로)로 한정")
    ap.add_argument("-k", "--top-k", type=int, default=6)
    ap.add_argument("--no-expand", action="store_true", help="쿼리 확장 생략 (Claude 호출 1회 절약)")
    ap.add_argument("--no-rerank", action="store_true", help="리랭킹 생략 (RRF 순서)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="검색 과정 로그를 stderr 로")
    args = ap.parse_args()

    r = HybridRetriever(expand=not args.no_expand, rerank=not args.no_rerank)
    hits = r.search(args.query, run=args.run, angle=args.angle, kind=args.kind, source=args.source, top_k=args.top_k)
    if args.verbose:
        for line in r.log:
            print(f"[rag] {line}", file=sys.stderr)

    if args.json:
        print(json.dumps({
            "query": args.query, "rerank_mode": r.rerank_mode, "log": r.log,
            "hits": [{"ref": h.ref(), "text": h.text, "rrf": round(h.rrf, 4), "rerank": h.rerank,
                      "source": h.meta.get("source"), "url": h.meta.get("url"), "chunk": h.meta.get("chunk"),
                      "kind": h.meta.get("kind"), "matched": h.matched_queries} for h in hits],
        }, ensure_ascii=False, indent=2))
        return

    if not hits:
        print("결과 없음. " + (r.log[-1] if r.log else ""))
        return
    print(f"# 검색: {args.query}  (결과 {len(hits)}개 · 리랭킹={r.rerank_mode or '없음'})\n")
    for i, h in enumerate(hits, 1):
        score = f"rerank={h.rerank:.3f}" if h.rerank is not None else f"rrf={h.rrf:.4f}"
        print(f"## [{i}] {h.ref()}  ({score})")
        print(h.text.strip())
        print()


if __name__ == "__main__":
    main()
