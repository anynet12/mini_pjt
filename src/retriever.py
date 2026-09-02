"""RAG 파이프라인 진입점 (파사드).

실제 구현은 `src/rag/` 에 있다:
    chunking.py  → 청크 분할 · 한국어 BM25 토크나이저
    store.py     → Chroma 저장소 (Titan 임베딩, run/angle/kind 메타 필터)
    retriever.py → 쿼리 확장(LCEL) → BM25 + 밀집 → RRF → 리랭킹(Bedrock Rerank / Claude 대체)
    faithfulness.py → 초안 주장 추출 → 근거 검색 → 지지 여부 판정

이 모듈은 자주 쓰는 진입점만 짧은 이름으로 다시 노출한다.

    from src.retriever import index_file, search, faithfulness
    hits = search("연금저축 이체 신청은 어디서 하나", run="2026-09-02", kind="transcript", top_k=5)
"""

from __future__ import annotations

from pathlib import Path

from src.rag.chunking import chunk_text, tokenize  # noqa: F401  (re-export)
from src.rag.faithfulness import FaithfulnessReport, check_faithfulness
from src.rag.retriever import Hit, HybridRetriever  # noqa: F401  (re-export)
from src.rag.store import KINDS, RagStore, make_where  # noqa: F401  (re-export)

_retriever: HybridRetriever | None = None


def get_retriever(*, expand: bool = True, rerank: bool = True) -> HybridRetriever:
    """프로세스 안에서 검색기(Chroma 클라이언트·체인)를 재사용한다."""
    global _retriever
    if _retriever is None or _retriever.expand != expand or _retriever.rerank != rerank:
        _retriever = HybridRetriever(expand=expand, rerank=rerank)
    return _retriever


def search(query: str, *, run: str | None = None, angle: str | None = None, kind: str | None = None,
           top_k: int = 6, expand: bool = True, rerank: bool = True) -> list[Hit]:
    return get_retriever(expand=expand, rerank=rerank).search(query, run=run, angle=angle, kind=kind, top_k=top_k)


def index_file(path: str | Path, *, run: str, kind: str, angle: str | None = None,
               title: str | None = None, url: str | None = None, max_chars: int = 600, overlap: int = 80) -> int:
    """파일 하나를 색인하고 청크 수를 돌려준다 (scripts/rag_index.py 와 같은 규칙)."""
    import sys

    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from scripts.rag_index import load_text, rel  # 메타 주석·HTML 처리 규칙 재사용

    p = Path(path)
    body, meta = load_text(p)
    return RagStore().index_chunks(
        chunk_text(body, max_chars=max_chars, overlap=overlap),
        run=run, angle=angle, kind=kind, source=rel(p),
        title=title or meta.get("title") or p.stem, url=url or meta.get("url"),
        extra={k: v for k, v in meta.items() if k in ("org", "checked")},
    )


def faithfulness(draft_text: str, *, draft_name: str, run: str, angle: str | None = None,
                 kind: str | None = None, top_k: int = 3) -> FaithfulnessReport:
    return check_faithfulness(draft_text, draft_name=draft_name, run=run, angle=angle, kind=kind,
                              retriever=get_retriever(expand=False, rerank=False), top_k=top_k)
