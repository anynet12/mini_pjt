"""하이브리드 검색기: 쿼리 확장 → BM25 + 밀집(Chroma) → RRF 융합 → 리랭킹 → top-k.

- 쿼리 확장: LCEL 체인(prompt | llm.with_structured_output(QueryExpansion)) 이 검색어 변형 2~3개를 만든다.
  원 질문에 없는 사실을 추가하지 않는 "표현 변형" 만 허용.
- BM25: rank_bm25(BM25Okapi) 를 필터된 코퍼스 위에서 즉석 구성 (코퍼스가 작아 매번 만들어도 빠르다).
- 밀집: Titan 임베딩 → Chroma cosine 검색.
- 융합: Reciprocal Rank Fusion (k=60). 쿼리 변형별 결과를 모두 합친다.
- 리랭킹: Bedrock Rerank → 권한 없으면(RerankUnavailable) Claude 리스트와이즈 리랭킹(LCEL) 으로 대체.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from backend.chains.bedrock import get_chat_model
from backend.rag.bedrock_embed import RerankUnavailable, bedrock_rerank, embed_text
from backend.rag.chunking import tokenize
from backend.rag.store import RagStore, make_where

RRF_K = 60


# ──────────────── LCEL 체인 1: 쿼리 확장 ────────────────

class QueryExpansion(BaseModel):
    queries: list[str] = Field(description="원 질문과 같은 뜻을 다른 표현으로 쓴 검색어 2~3개 (한국어)")


_EXPAND_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "당신은 검색어 확장기다. 사용자의 질문을 같은 의미의 다른 표현 2~3개로 바꿔라. "
               "동의어·줄임말·기관 용어(예: '이체'↔'계좌이전', 'IRP'↔'개인형퇴직연금')를 활용하고, "
               "원 질문에 없는 조건·사실을 덧붙이지 마라. 각 변형은 15자 안팎의 검색어 형태."),
    ("human", "{query}"),
])


def build_expand_chain(llm=None):
    llm = llm or get_chat_model(temperature=0.2, max_tokens=300)
    return (_EXPAND_PROMPT | llm.with_structured_output(QueryExpansion)).with_config(run_name="query_expansion")


# ──────────────── LCEL 체인 2: Claude 리스트와이즈 리랭킹 (Rerank 대체) ────────────────

class RerankResult(BaseModel):
    ranked_ids: list[int] = Field(description="질문에 답하는 데 유용한 순서로 정렬한 후보 번호. 관련 없는 후보는 제외")


_RERANK_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "당신은 검색 결과 리랭커다. 질문에 실제로 답이 들어 있는 후보를 앞에, 키워드만 겹치고 답이 없는 "
               "후보는 뒤로 보내거나 제외한다. 후보 번호만 반환한다."),
    ("human", "질문: {query}\n\n후보:\n{candidates}\n\n상위 {top_n}개까지 번호를 정렬해 반환하라."),
])


def build_llm_rerank_chain(llm=None):
    llm = llm or get_chat_model(temperature=0.0, max_tokens=300)
    return (_RERANK_PROMPT | llm.with_structured_output(RerankResult)).with_config(run_name="llm_rerank")


# ──────────────── 검색기 ────────────────

@dataclass
class Hit:
    id: str
    text: str
    meta: dict
    rrf: float = 0.0
    rerank: float | None = None
    matched_queries: list[str] = field(default_factory=list)

    def ref(self) -> str:
        m = self.meta
        base = m.get("title") or m.get("source", "?")
        return f"{base}#{m.get('chunk', '?')}" + (f" ({m['url']})" if m.get("url") else "")


class HybridRetriever:
    def __init__(self, store: RagStore | None = None, *, expand: bool = True, rerank: bool = True,
                 n_dense: int = 20, n_bm25: int = 20, verbose: bool = False):
        self.store = store or RagStore()
        self.expand = expand
        self.rerank = rerank
        self.n_dense = n_dense
        self.n_bm25 = n_bm25
        self.verbose = verbose
        self.rerank_mode: str | None = None  # "bedrock" | "claude" | None
        self._expand_chain = None
        self._llm_rerank_chain = None
        self.log: list[str] = []

    def _note(self, msg: str):
        self.log.append(msg)

    # ── 쿼리 확장
    def expand_queries(self, query: str) -> list[str]:
        if not self.expand:
            return [query]
        try:
            self._expand_chain = self._expand_chain or build_expand_chain()
            variants = self._expand_chain.invoke({"query": query}).queries
        except Exception as e:  # noqa: BLE001 — 확장 실패는 치명적이지 않다
            self._note(f"쿼리 확장 실패, 원 질문만 사용: {type(e).__name__}")
            return [query]
        out = [query] + [v.strip() for v in variants if v.strip() and v.strip() != query]
        self._note(f"쿼리 확장: {out}")
        return out[:4]

    # ── 후보 생성
    def _bm25(self, queries: list[str], corpus: list[dict]) -> dict[str, list[tuple[str, int]]]:
        if not corpus:
            return {}
        bm = BM25Okapi([tokenize(d["text"]) for d in corpus])
        ranked: dict[str, list[tuple[str, int]]] = {}
        for q in queries:
            scores = bm.get_scores(tokenize(q))
            order = sorted(range(len(corpus)), key=lambda i: -scores[i])
            ranked[q] = [(corpus[i]["id"], r) for r, i in enumerate(order[: self.n_bm25]) if scores[i] > 0]
        return ranked

    def _dense(self, queries: list[str], where) -> tuple[dict[str, list[tuple[str, int]]], dict[str, dict]]:
        ranked: dict[str, list[tuple[str, int]]] = {}
        docs: dict[str, dict] = {}
        for q in queries:
            hits = self.store.dense_search(embed_text(q), k=self.n_dense, where=where)
            ranked[q] = [(h["id"], r) for r, h in enumerate(hits)]
            for h in hits:
                docs[h["id"]] = h
        return ranked, docs

    # ── 융합
    @staticmethod
    def _rrf(rank_lists: list[tuple[str, list[tuple[str, int]]]]) -> dict[str, tuple[float, list[str]]]:
        fused: dict[str, tuple[float, list[str]]] = {}
        for label, ranking in rank_lists:
            for doc_id, rank in ranking:
                score, qs = fused.get(doc_id, (0.0, []))
                fused[doc_id] = (score + 1.0 / (RRF_K + rank + 1), qs + [label])
        return fused

    # ── 리랭킹
    def _rerank(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]:
        if not self.rerank or len(hits) <= 1:
            self.rerank_mode = None
            return hits[:top_k]
        docs = [h.text for h in hits]
        if self.rerank_mode != "claude":
            try:
                ranked = bedrock_rerank(query, docs, top_n=top_k)
                self.rerank_mode = "bedrock"
                out = []
                for idx, score in ranked:
                    h = hits[idx]
                    h.rerank = score
                    out.append(h)
                self._note(f"리랭킹: Bedrock Rerank ({len(out)}개)")
                return out
            except RerankUnavailable as e:
                self._note(f"Bedrock Rerank 불가 → Claude 리랭킹으로 대체 ({str(e)[:80]})")
                self.rerank_mode = "claude"
        try:
            self._llm_rerank_chain = self._llm_rerank_chain or build_llm_rerank_chain()
            cands = "\n".join(f"[{i}] {h.text[:600].replace(chr(10), ' ')}" for i, h in enumerate(hits))
            res = self._llm_rerank_chain.invoke({"query": query, "candidates": cands, "top_n": top_k})
            seen, out = set(), []
            for i in res.ranked_ids:
                if 0 <= i < len(hits) and i not in seen:
                    seen.add(i)
                    h = hits[i]
                    h.rerank = float(len(hits) - len(out))  # 순위 기반 점수
                    out.append(h)
            if not out:
                raise ValueError("빈 리랭킹 결과")
            return out[:top_k]
        except Exception as e:  # noqa: BLE001
            self._note(f"Claude 리랭킹 실패, RRF 순서 유지: {type(e).__name__}")
            return hits[:top_k]

    # ── 진입점
    def search(self, query: str, *, run: str | None = None, angle: str | None = None, kind: str | None = None,
               source: str | None = None, top_k: int = 6) -> list[Hit]:
        self.log = []
        where = make_where(run=run, angle=angle, kind=kind, source=source)
        corpus = self.store.corpus(where=where)
        if not corpus:
            self._note(f"필터에 맞는 문서 없음: run={run} angle={angle} kind={kind}")
            return []
        self._note(f"코퍼스 {len(corpus)}청크 (run={run or '*'} angle={angle or '*'} kind={kind or '*'})")
        queries = self.expand_queries(query)

        bm25_ranked = self._bm25(queries, corpus)
        dense_ranked, dense_docs = self._dense(queries, where)
        rank_lists = [(f"bm25:{q}", r) for q, r in bm25_ranked.items()] + [(f"dense:{q}", r) for q, r in dense_ranked.items()]
        fused = self._rrf(rank_lists)

        by_id = {d["id"]: d for d in corpus}
        by_id.update(dense_docs)
        hits = [Hit(id=i, text=by_id[i]["text"], meta=by_id[i]["meta"], rrf=s, matched_queries=qs)
                for i, (s, qs) in fused.items() if i in by_id]
        hits.sort(key=lambda h: -h.rrf)
        pool = hits[: max(top_k * 3, 12)]
        self._note(f"후보 {len(hits)}개 → 리랭킹 풀 {len(pool)}개")
        return self._rerank(query, pool, top_k)
