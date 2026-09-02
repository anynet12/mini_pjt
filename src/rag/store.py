"""Chroma 벡터 저장소 래퍼.

- 위치: `CHROMA_DB_DIR`(.env/환경변수) 또는 프로젝트 루트 `chroma_db/` (gitignore).
- 컬렉션 하나(`blog_agent`, cosine). 실행(run)·앵글(angle)·종류(kind)·출처(source) 메타데이터로
  검색 범위를 좁힌다. 임베딩은 Bedrock 에서 직접 계산해 넘기므로 Chroma 내장 모델(onnx)은 쓰지 않는다.
- 문서 ID = sha1(source + chunk index) → 같은 파일을 다시 색인하면 덮어써진다(upsert).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
from pathlib import Path

from src.chains.bedrock import REPO_ROOT  # .env 로딩 부수효과 포함
from src.rag.bedrock_embed import embed_texts
from src.rag.chunking import Chunk

COLLECTION = "blog_agent"
KINDS = ("transcript", "insights", "research", "source", "post", "draft")


def db_dir() -> Path:
    return Path(os.environ.get("CHROMA_DB_DIR") or (REPO_ROOT / "chroma_db"))


def make_where(run: str | None = None, angle: str | None = None, kind: str | None = None, source: str | None = None):
    conds = [{"run": run} if run else None, {"angle": angle} if angle else None,
             {"kind": kind} if kind else None, {"source": source} if source else None]
    conds = [c for c in conds if c]
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


class RagStore:
    def __init__(self, path: Path | None = None):
        import chromadb

        self.path = Path(path) if path else db_dir()
        self.path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.path))
        try:
            self.col = self.client.get_or_create_collection(COLLECTION, configuration={"hnsw": {"space": "cosine"}})
        except Exception:  # noqa: BLE001 — 구버전 API
            self.col = self.client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    # ── 색인 ──
    def index_chunks(self, chunks: list[Chunk], *, run: str | None, angle: str | None, kind: str, source: str,
                     title: str | None = None, url: str | None = None, extra: dict | None = None) -> int:
        if kind not in KINDS:
            raise ValueError(f"kind 는 {KINDS} 중 하나여야 합니다: {kind}")
        if not chunks:
            return 0
        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        ids, docs, metas = [], [], []
        for c in chunks:
            ids.append(hashlib.sha1(f"{source}#{c.index}".encode("utf-8")).hexdigest())
            docs.append(c.text)
            meta = {"run": run or "", "angle": angle or "", "kind": kind, "source": source,
                    "chunk": c.index, "indexed_at": now}
            if title:
                meta["title"] = title
            if url:
                meta["url"] = url
            if extra:
                meta.update({k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool))})
            metas.append(meta)
        # 같은 source 의 옛 청크(개수가 줄었을 때 남는 꼬리)를 먼저 지운다
        self.col.delete(where={"source": source})
        embs = embed_texts(docs)
        self.col.upsert(ids=ids, embeddings=embs, documents=docs, metadatas=metas)
        return len(ids)

    # ── 조회 ──
    def dense_search(self, query_embedding: list[float], k: int = 20, where=None) -> list[dict]:
        n = min(k, max(self.col.count(), 1))
        r = self.col.query(query_embeddings=[query_embedding], n_results=n, where=where,
                           include=["documents", "metadatas", "distances"])
        out = []
        for i, doc_id in enumerate(r["ids"][0]):
            out.append({"id": doc_id, "text": r["documents"][0][i], "meta": r["metadatas"][0][i],
                        "dense_score": 1.0 - float(r["distances"][0][i])})
        return out

    def corpus(self, where=None, limit: int | None = None) -> list[dict]:
        r = self.col.get(where=where, limit=limit, include=["documents", "metadatas"])
        return [{"id": i, "text": d, "meta": m} for i, d, m in zip(r["ids"], r["documents"], r["metadatas"])]

    def count(self, where=None) -> int:
        if where is None:
            return self.col.count()
        return len(self.col.get(where=where, include=[])["ids"])

    def sources(self, where=None) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.col.get(where=where, include=["metadatas"])["metadatas"]:
            counts[m.get("source", "?")] = counts.get(m.get("source", "?"), 0) + 1
        return counts

    def delete_source(self, source: str) -> None:
        self.col.delete(where={"source": source})
