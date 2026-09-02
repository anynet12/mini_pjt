"""초안 faithfulness 검사 (RAGAS faithfulness 류 · 패턴 12 정량 지표).

1) 초안에서 검증 가능한 사실 주장(수치·절차·조건·인용)을 추출한다  — LCEL 체인 (Pydantic Claims)
2) 주장마다 HybridRetriever 로 근거 청크를 찾는다                     — 패턴 3 재사용
3) 주장 + 근거 청크를 한 번에 넘겨 지지 여부를 판정한다                — LCEL 체인 (Pydantic Judgement)
   supported / partially / unsupported. unsupported 는 "근거 문서 밖의 사실" = 이 프로젝트의 최상위 금지.

faithfulness = supported / 전체 (partially 는 0.5). 호출 수는 초안당 Claude 2회 + 임베딩 (주장 수 × 쿼리 수).
"""

from __future__ import annotations

import datetime as _dt
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from backend.chains.bedrock import get_chat_model
from backend.rag.retriever import HybridRetriever


class Claim(BaseModel):
    text: str = Field(description="초안 속 검증 가능한 사실 주장 1개 (원문 표현 유지, 한 문장)")
    section: str = Field(default="", description="주장이 있는 소제목 (없으면 빈 문자열)")


class Claims(BaseModel):
    claims: list[Claim] = Field(description="수치·절차·조건·기관명·인용 등 근거 대조가 필요한 주장. 의견·비유·독자 호소는 제외. 최대 20개")


class ClaimJudgement(BaseModel):
    claim_index: int
    verdict: Literal["supported", "partially", "unsupported"]
    evidence_ref: str = Field(default="", description="근거로 삼은 청크 참조(있으면)")
    note: str = Field(default="", description="부분 지지·불지지 사유 한 줄")


class Judgement(BaseModel):
    judgements: list[ClaimJudgement]


class FaithfulnessReport(BaseModel):
    draft: str
    run: str | None = None
    angle: str | None = None
    kind_filter: str | None = None
    checked_at: str = Field(default_factory=lambda: _dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    rerank_mode: str | None = None
    n_claims: int
    supported: int
    partially: int
    unsupported: int
    faithfulness: float
    items: list[dict]


_EXTRACT = ChatPromptTemplate.from_messages([
    ("system", "당신은 팩트체커다. 블로그 초안에서 근거 문서와 대조해야 할 사실 주장만 뽑는다. "
               "수치·비율·금액·기간·절차 단계·자격 조건·기관/제도 이름·누가 무엇을 했다는 서술이 대상이다. "
               "글쓴이의 의견, 비유, 독자에게 건네는 말, 일반 상식은 제외한다. 주장은 원문 표현을 살려 한 문장으로."),
    ("human", "{draft}"),
])

_JUDGE = ChatPromptTemplate.from_messages([
    ("system", "당신은 근거 대조 심사관이다. 각 주장이 제시된 근거 청크만으로 뒷받침되는지 판정한다.\n"
               "- supported: 근거가 주장을 그대로(또는 동치로) 말한다\n"
               "- partially: 방향은 맞지만 수치·조건·범위가 다르거나 일부만 있다\n"
               "- unsupported: 근거 어디에도 없다 (일반 상식이라도 근거에 없으면 unsupported)\n"
               "근거 밖 지식으로 보충하지 마라."),
    ("human", "{bundle}"),
])


def build_extract_chain(llm=None):
    llm = llm or get_chat_model(temperature=0.0, max_tokens=2500)
    return (_EXTRACT | llm.with_structured_output(Claims)).with_config(run_name="claim_extraction")


def build_judge_chain(llm=None):
    llm = llm or get_chat_model(temperature=0.0, max_tokens=3000)
    return (_JUDGE | llm.with_structured_output(Judgement)).with_config(run_name="claim_judgement")


def check_faithfulness(draft_text: str, *, draft_name: str, run: str | None, angle: str | None,
                       kind: str | None = None, retriever: HybridRetriever | None = None,
                       top_k: int = 3, max_claims: int = 20) -> FaithfulnessReport:
    # 기본은 확장·리랭킹 없이 RRF 상위만 쓴다 — 주장 수만큼 검색이 돌아가므로 Claude 대체 리랭킹이면
    # 호출이 주장 수만큼 늘어난다. Bedrock Rerank 권한이 있으면 CLI --rerank 로 켠다.
    retriever = retriever or HybridRetriever(expand=False, rerank=False)
    claims = build_extract_chain().invoke({"draft": draft_text[:30000]}).claims[:max_claims]
    if not claims:
        return FaithfulnessReport(draft=draft_name, run=run, angle=angle, kind_filter=kind, n_claims=0,
                                  supported=0, partially=0, unsupported=0, faithfulness=1.0, items=[])

    contexts: list[list] = []
    for c in claims:
        contexts.append(retriever.search(c.text, run=run, angle=angle, kind=kind, top_k=top_k))

    parts = []
    for i, (c, hits) in enumerate(zip(claims, contexts)):
        ev = "\n".join(f"    - [{h.ref()}] {h.text[:500].replace(chr(10), ' ')}" for h in hits) or "    (근거 없음)"
        parts.append(f"[{i}] 주장: {c.text}\n  근거:\n{ev}")
    judged = build_judge_chain().invoke({"bundle": "\n\n".join(parts)}).judgements
    by_idx = {j.claim_index: j for j in judged}

    items, counts = [], {"supported": 0, "partially": 0, "unsupported": 0}
    for i, (c, hits) in enumerate(zip(claims, contexts)):
        j = by_idx.get(i)
        verdict = j.verdict if j else "unsupported"
        counts[verdict] += 1
        items.append({"index": i, "claim": c.text, "section": c.section, "verdict": verdict,
                      "evidence_ref": (j.evidence_ref if j else "") or (hits[0].ref() if hits else ""),
                      "note": j.note if j else "판정 누락"})
    n = len(claims)
    score = (counts["supported"] + 0.5 * counts["partially"]) / n
    return FaithfulnessReport(draft=draft_name, run=run, angle=angle, kind_filter=kind, rerank_mode=retriever.rerank_mode,
                              n_claims=n, supported=counts["supported"], partially=counts["partially"],
                              unsupported=counts["unsupported"], faithfulness=round(score, 3), items=items)
