"""reviewer 판정문 → 구조화 판정(ReviewRecord) LCEL 체인 (패턴 1 + 패턴 12).

reviewer 서브에이전트는 마크다운 자유 형식으로 판정을 낸다. 오케스트레이터가 그 전문을
`posts/{date}/a{N}/review_vN.md` 로 저장하면, 이 체인이 Bedrock Claude 의 구조화 출력으로
Pydantic 스키마(ReviewVerdict)에 맞춰 파싱하고, 텍스트에서 정규식으로 뽑은 확정 값(판정·종합점수)과
대조해 불일치를 바로잡는다. 결과 JSON 의 `issues[].stage` 가 재작업 라우팅의 기계적 입력이 된다.

체인 구조 (LCEL):
    입력 dict{review_text, ...meta}
      | assign(text_facts = 정규식 추출)                      ← 결정적 단계
      | assign(result = prompt | llm.with_structured_output(ReviewVerdict).with_retry())
      | reconcile → ReviewRecord                              ← 검증·보정 단계
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough
from pydantic import BaseModel, Field, model_validator

from .bedrock import get_chat_model

# ──────────────────────────── 스키마 (Pydantic) ────────────────────────────

Stage = Literal["writer", "image", "finalizer"]
Criterion = Literal["A", "B", "C", "D"]
Severity = Literal["critical", "major", "minor"]

WEIGHTS = {"A": 0.25, "B": 0.35, "C": 0.25, "D": 0.15}
PASS_THRESHOLD = 70


class Issue(BaseModel):
    """reviewer 가 지적한 문제 하나. 재작업 라우팅의 최소 단위."""

    criterion: Criterion = Field(description="평가 기준 코드: A 양식/형식, B 독자 흡인력, C 문맥 정합성, D 제작 흔적")
    severity: Severity = Field(description="critical=단독으로 FAIL 사유, major=수정 필요, minor=개선 제안")
    stage: Stage = Field(description="재작업을 맡을 원인 단계")
    problem: str = Field(description="무엇이 왜 문제인가 (원문 인용 포함 가능)")
    instruction: str = Field(description="해당 단계가 바로 실행할 수 있는 구체적 개선 지시")


class Scores(BaseModel):
    a_format: int = Field(ge=0, le=100, description="A 양식/형식 점수")
    b_appeal: int = Field(ge=0, le=100, description="B 독자 흡인력 점수")
    c_coherence: int = Field(ge=0, le=100, description="C 문맥 정합성 점수")
    d_traces: int = Field(ge=0, le=100, description="D 제작 흔적 점수(흔적이 없을수록 높음)")
    total: int = Field(ge=0, le=100, description="종합 점수 (판정문에 적힌 값 그대로)")

    def weighted(self) -> int:
        return round(
            self.a_format * WEIGHTS["A"] + self.b_appeal * WEIGHTS["B"]
            + self.c_coherence * WEIGHTS["C"] + self.d_traces * WEIGHTS["D"]
        )


class ReviewVerdict(BaseModel):
    """LLM 이 판정문에서 추출하는 구조화 결과."""

    verdict: Literal["PASS", "FAIL"] = Field(description="판정문 맨 위 '## 판정:' 값")
    scores: Scores
    strengths: list[str] = Field(default_factory=list, description="강점 (짧은 문장)")
    weaknesses: list[str] = Field(default_factory=list, description="약점 (짧은 문장)")
    issues: list[Issue] = Field(default_factory=list, description="재작업 지시 목록. PASS 면 보통 비어 있거나 minor 만")
    summary: str = Field(description="PASS 면 제출 요약(핵심 강점 2~3줄), FAIL 이면 한 줄 총평")

    @model_validator(mode="after")
    def _fail_needs_issue(self):
        if self.verdict == "FAIL" and not self.issues:
            raise ValueError("FAIL 판정에는 재작업 지시(issues)가 최소 1개 있어야 합니다")
        return self


class ReviewRecord(BaseModel):
    """파일로 저장되는 최종 레코드 = 메타데이터 + 구조화 판정 + 보정 이력."""

    run: str | None = None
    angle: str | None = None
    version: int | None = None
    source_file: str | None = None
    reviewed_at: str = Field(default_factory=lambda: _dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    model: str | None = None
    text_facts: dict = Field(default_factory=dict, description="정규식으로 판정문에서 직접 뽑은 확정 값")
    adjustments: list[str] = Field(default_factory=list, description="LLM 출력과 확정 값이 달라 보정한 내역")
    consistency_warnings: list[str] = Field(default_factory=list, description="판정 규칙과 어긋나는 점(오케스트레이터가 확인)")
    result: ReviewVerdict

    # 오케스트레이터가 바로 쓰는 파생 필드
    @property
    def rework_stages(self) -> list[Stage]:
        order = {"writer": 0, "image": 1, "finalizer": 2}
        stages = {i.stage for i in self.result.issues if i.severity != "minor"}
        return sorted(stages, key=order.get)


# ──────────────────────────── 결정적 추출 ────────────────────────────

VERDICT_RE = re.compile(r"##\s*판정\s*[:：]\s*(PASS|FAIL)", re.I)
TOTAL_RE = re.compile(r"종합\s*[:：]?\s*(\d{1,3})\s*/\s*100")
LEGACY_TOTAL_RE = re.compile(r"점수\s*[:：]\s*(\d{1,3})\s*/\s*100")
CRIT_RE = {
    "A": re.compile(r"A\s*[\.\s]?\s*양식[^\n]*?[:：]\s*(\d{1,3})"),
    "B": re.compile(r"B\s*[\.\s]?\s*(?:독자\s*)?흡인력[^\n]*?[:：]\s*(\d{1,3})"),
    "C": re.compile(r"C\s*[\.\s]?\s*문맥[^\n]*?[:：]\s*(\d{1,3})"),
    "D": re.compile(r"D\s*[\.\s]?\s*제작[^\n]*?[:：]\s*(\d{1,3})"),
}


def extract_text_facts(review_text: str) -> dict:
    facts: dict = {}
    m = VERDICT_RE.search(review_text)
    if m:
        facts["verdict"] = m.group(1).upper()
    m = TOTAL_RE.search(review_text) or LEGACY_TOTAL_RE.search(review_text)
    if m:
        facts["total"] = int(m.group(1))
    for code, pat in CRIT_RE.items():
        m = pat.search(review_text)
        if m:
            facts[f"score_{code}"] = int(m.group(1))
    return facts


# ──────────────────────────── 프롬프트 ────────────────────────────

SYSTEM_PROMPT = """당신은 블로그 품질 게이트(reviewer)가 낸 판정문을 구조화하는 파서다.
판정문에 적힌 내용만 옮긴다. 없는 사실을 추가하거나 점수를 임의로 매기지 않는다.

규칙:
- verdict 는 '## 판정:' 줄의 값이다.
- scores 는 '기준별 점수' 섹션의 A/B/C/D 점수와 '종합' 점수를 그대로 옮긴다. 기준별 점수가 없는
  옛 형식이면 '점수: NN / 100' 을 total 로 쓰고, A~D 는 양식 점검·흡인력 평가 서술을 근거로
  가장 가까운 정수로 추정하되 total 과 크게 어긋나지 않게 한다.
- issues 는 '재작업 지시' 섹션의 항목을 하나씩 옮긴다. 원인 단계(stage)는 판정문에 적힌
  writer/image/finalizer 를 그대로 쓴다. severity 는 판정문이 'FAIL 사유·치명적·반드시' 로
  표현하면 critical, 수정 지시면 major, '제안·권장·사소' 면 minor.
- criterion 은 지적 내용이 어느 기준에 속하는지로 정한다: 제목·메타·태그·구조·HTML·이미지 규격·
  문체 → A, 훅·가독성·가치·AI 티 → B, 허수 대비·약속 불이행·논리 비약·삭제 잔재·주체 혼선·
  작업 용어·톤 → C, 자막/리서치 흔적·귀속 누락 → D.
- PASS 인데 '다음에 더 좋아질 사소한 제안' 이 있으면 minor issue 로 넣어도 된다.
- summary 는 PASS 면 '제출 요약' 을, FAIL 이면 약점을 한 줄로 요약한다."""

PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", "다음 reviewer 판정문을 구조화하라.\n\n<review>\n{review_text}\n</review>"),
])


# ──────────────────────────── 보정 단계 ────────────────────────────

def reconcile(x: dict) -> ReviewRecord:
    result: ReviewVerdict = x["result"]
    facts: dict = x["text_facts"]
    adjustments: list[str] = []
    warnings: list[str] = []

    # 1) 판정은 텍스트가 진실. LLM 이 다르게 읽었으면 텍스트 값으로 덮어쓴다.
    if "verdict" in facts and facts["verdict"] != result.verdict:
        adjustments.append(f"verdict: LLM={result.verdict} → 판정문={facts['verdict']}")
        result = result.model_copy(update={"verdict": facts["verdict"]})

    # 2) 종합 점수도 텍스트 값 우선
    if "total" in facts and facts["total"] != result.scores.total:
        adjustments.append(f"total: LLM={result.scores.total} → 판정문={facts['total']}")
        result = result.model_copy(update={"scores": result.scores.model_copy(update={"total": facts["total"]})})
    for code, field in (("A", "a_format"), ("B", "b_appeal"), ("C", "c_coherence"), ("D", "d_traces")):
        key = f"score_{code}"
        if key in facts and facts[key] != getattr(result.scores, field):
            adjustments.append(f"{field}: LLM={getattr(result.scores, field)} → 판정문={facts[key]}")
            result = result.model_copy(update={"scores": result.scores.model_copy(update={field: facts[key]})})

    # 3) 판정 규칙 정합성 검사 (고치지 않고 경고만 — 판정 자체는 reviewer 의 권한)
    has_critical = any(i.severity == "critical" for i in result.issues)
    if result.verdict == "PASS" and has_critical:
        warnings.append("PASS 판정인데 critical 지적이 있음 — reviewer 판정 기준과 어긋남")
    if result.verdict == "PASS" and result.scores.total < PASS_THRESHOLD:
        warnings.append(f"PASS 판정인데 종합 {result.scores.total} < 통과선 {PASS_THRESHOLD}")
    if result.verdict == "FAIL" and not has_critical and result.scores.total >= PASS_THRESHOLD:
        warnings.append(f"FAIL 판정인데 critical 지적이 없고 종합 {result.scores.total} ≥ {PASS_THRESHOLD}")
    # 기준별 점수가 판정문에 실제로 적혀 있을 때만 가중합 정합성을 본다 (옛 형식은 LLM 추정치라 제외)
    if all(f"score_{c}" in facts for c in "ABCD"):
        weighted = result.scores.weighted()
        if abs(weighted - result.scores.total) > 5:
            warnings.append(f"기준별 가중합 {weighted} 과 종합 {result.scores.total} 차이가 5점 초과")

    return ReviewRecord(
        run=x.get("run"), angle=x.get("angle"), version=x.get("version"), source_file=x.get("source_file"),
        model=x.get("model"), text_facts=facts, adjustments=adjustments,
        consistency_warnings=warnings, result=result,
    )


# ──────────────────────────── 체인 조립 ────────────────────────────

def build_review_chain(llm=None) -> Runnable:
    """입력: {"review_text": str, "run"?, "angle"?, "version"?, "source_file"?} → ReviewRecord"""
    llm = llm or get_chat_model(temperature=0.0, max_tokens=3000)
    structured = llm.with_structured_output(ReviewVerdict).with_retry(stop_after_attempt=2)
    model_name = getattr(llm, "model_id", None) or getattr(llm, "model", None)

    return (
        RunnablePassthrough.assign(
            text_facts=RunnableLambda(lambda x: extract_text_facts(x["review_text"])),
            model=RunnableLambda(lambda _: model_name),
        )
        | RunnablePassthrough.assign(result=PROMPT | structured)
        | RunnableLambda(reconcile)
    ).with_config(run_name="review_chain")


def parse_review_text(review_text: str, **meta) -> ReviewRecord:
    return build_review_chain().invoke({"review_text": review_text, **meta})
