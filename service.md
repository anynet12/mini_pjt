# 서비스 구조 (blog-agent)

> CLAUDE.md가 정의하는 오케스트레이션 규칙의 "구조 요약 + LLM 에이전트 패턴 관점 점검" 문서.
> 실제 동작 규칙의 단일 진실 공급원은 여전히 CLAUDE.md이며, 이 문서는 그 위에 아키텍처
> 관점의 조감도를 얹은 것이다. (2026-09-02 갱신 — 패턴 1·3·11·12 도입 반영)

## 1. 트랙 개요

두 개의 입력 트랙이 하나의 파이프라인(writer→image→finalizer→reviewer→poster)을 공유한다.

- **영상 기반 트랙**: 유튜브 URL → `researcher`(자막 추출·인사이트·앵글 도출) → 자막을 RAG 색인
- **절차형(가이드) 트랙**: 저경쟁 How-to 키워드 → `guide-researcher`(키워드 발굴·공식 출처 리서치,
  유일하게 웹 검색 허용) → 읽은 공식 페이지 원문을 `sources/`에 저장·색인하고 근거 검색으로 research.md 작성

## 2. 계층 구성

### 2-1. 서브에이전트 (Multi-Agent Supervisor, `.claude/agents/*.md`)

```
오케스트레이터(메인 에이전트)
  ├─ researcher / guide-researcher   (자료 수집 · guide-researcher는 sources/ 저장 + RAG 색인·검색)
  ├─ writer         (초안 작성 · 섹션마다 rag_search.py로 근거 청크 확인)
  ├─ image          (SVG 이미지 제작)
  ├─ finalizer      (퇴고 + HTML 빌드)
  ├─ reviewer       (품질 게이트 · A~D 루브릭 점수 + PASS/FAIL, faithfulness 결과 반영, 직접 수정 안 함)
  └─ poster         (티스토리 자동 발행)
```

오케스트레이터가 각 서브에이전트를 호출·감독하며, 앵글(또는 키워드)이 N개면 N개
파이프라인을 **앵글별 독립 병렬**로 진행한다(단계별 일괄 동기화 없음). 진행 상태는
앵글×단계 매트릭스로 매 호출/완료/재작업마다 다시 보고된다.

### 2-2. 보조 계층 (Python, 오케스트레이터·서브에이전트가 Bash로 호출)

| 계층 | 위치 | 역할 | 패턴 |
|---|---|---|---|
| LCEL 체인 | `backend/chains/` | Bedrock `ChatBedrockConverse` + Pydantic 구조화 출력. `review_chain.py`(판정 구조화), RAG의 쿼리 확장·리랭킹·faithfulness 체인이 같은 팩토리(`bedrock.py`)를 쓴다 | 1 |
| RAG | `backend/rag/` | 청크 분할 → Titan 임베딩 → Chroma 색인, BM25+밀집 하이브리드 검색, RRF, 리랭킹, faithfulness 검사 | 3 · 12 |
| 스크립트 | `scripts/` | `rag_index.py` `rag_search.py` `check_faithfulness.py` `parse_review.py` `eval_report.py` `trace_report.py` + 기존 `cleanup.py` `svg_to_png.py` `tistory_post.py` | — |
| 트레이싱 hook | `.claude/settings.json` → `scripts/trace_hook.py` | 세션·프롬프트·도구·서브에이전트 이벤트를 `traces/{date}/{session}.jsonl`로 자동 기록 | 11 |
| 설정 | `.env`(gitignore) · `requirements.txt` | Bedrock 자격증명·모델 ID(Claude/Titan/Rerank)·`CHROMA_DB_DIR` | — |

## 3. 실행 단위 · 파일 구조

- `posts/{date}/` — 실행 단위. `transcript.txt`/`insights.md`(영상) 또는
  `keyword_candidates.md`(가이드)를 공유 자료로 둔다.
- `posts/{date}/a{N}/` 또는 `g{N}/` — 앵글/키워드별 산출물 세트. 버전은 단계별 독립, "현재본" = 가장 큰 번호.
  - 집필·빌드: `draft_vN.md` → `images_vN/` → `post_vN.html` → `post_tistory.html`/`post_naver.html`
  - 근거·평가: `sources/`(가이드 트랙, 공식 페이지 원문) · `research.md` · `faithfulness_vN.json`
    · `review_vN.md`(판정 전문) · `review_vN.json`(구조화 판정) · `review_log.md/.jsonl`(누적)
- 실행 밖에 남는 것: `chroma_db/`(RAG 색인 — 발행글은 다음 실행의 키워드 중복 회피에 재사용),
  `traces/`(트레이스). 둘 다 gitignore이며 `posts/` 7일 retention의 영향을 받지 않는다.

## 4. 한 앵글의 흐름 (패턴이 꽂히는 지점)

```
researcher/guide-researcher ──▶ rag_index.py (자막 / sources·research.md)          [3]
        │
writer ── rag_search.py로 섹션별 근거 청크 확인하며 draft_vN.md 작성                 [3]
        │
image → finalizer → post_vN.html
        │
check_faithfulness.py ── 주장 추출(LCEL) → 근거 검색(RAG) → 지지 여부 판정          [1·3·12]
        │                 → faithfulness_vN.json
reviewer ── A~D 루브릭 채점 + PASS/FAIL, unsupported 주장은 D critical              [12]
        │
parse_review.py ── LCEL 체인이 판정문 → review_vN.json (verdict·scores·issues)     [1]
        │            review_log.md/.jsonl 누적 (PASS·FAIL 모두)
FAIL ─▶ issues[].stage로 원인 단계 자동 재작업(최대 2회) → 재빌드 → 재심사
PASS ─▶ poster 발행 → rag_index.py --kind post (과거 글 코퍼스)                    [3]
        │
실행 종료 ─▶ eval_report.py (PASS율·기준별 평균·반복 지적) + trace_report.py       [11·12]
             (스팬 duration·앵글×단계 매트릭스·도구 통계) → 개선 방향 1회 보고
```

- 전 과정에서 hook이 이벤트를 `traces/`에 기록한다(파이프라인 영향 없음, exit 0 고정). [11]
- Bedrock 호출이 실패(스로틀·권한)해도 멈추지 않는다: 쿼리 확장 실패 → 원 질문만, Rerank 권한 없음 →
  Claude 리랭킹, 그것도 실패 → RRF 순서, parse_review 실패 → 오케스트레이터가 판정문 직접 해석,
  faithfulness 실패 → 미검사로 표시하고 진행.

## 5. 발행

`poster`가 `scripts/tistory_post.py`로 `post_tistory.html`(텍스트+형광펜+태그)을 자동
발행. 이미지는 자동 발행 경로에 못 들어가 사람이 HTML 모드로 수동 붙여넣기 + 대표이미지
수동 등록. 네이버는 `post_naver.html`(이미지 자리표시자)을 사람이 반자동으로 옮김. 발행 직후
발행글을 `kind=post`로 RAG 색인해 다음 실행의 키워드 발굴 때 중복 여부를 검색으로 확인한다.

## 6. 12개 LLM 에이전트 패턴 사용 현황

| # | 항목 | 사용 여부 | 비고 (사용 사례 / 미사용·제한 사유) |
|---|---|---|---|
| 1 | LCEL chain (Pydantic 구조화 출력) | 사용 | `backend/chains/review_chain.py` — reviewer 판정문을 `RunnablePassthrough.assign(정규식 추출) \| prompt \| ChatBedrockConverse.with_structured_output(ReviewVerdict).with_retry() \| reconcile` LCEL 파이프로 Pydantic 스키마(`verdict`·`scores{A,B,C,D,total}`·`issues[{criterion,severity,stage,…}]`)에 구조화. 오케스트레이터가 매 심사마다 `scripts/parse_review.py`로 호출하고 `issues[].stage`로 재작업을 라우팅. 같은 방식의 체인이 RAG 쿼리 확장(`QueryExpansion`)·Claude 리랭킹(`RerankResult`)·faithfulness(`Claims`/`Judgement`)에도 쓰임 |
| 2 | ReAct (도구 자율 선택) | 사용 | 각 서브에이전트가 부여된 도구 집합 안에서 상황에 맞게 자율 선택 — 예: `researcher`가 자막 추출에 `Bash`(yt-dlp)를, 앵글 정리에 `Write`를 스스로 판단해 사용. writer는 섹션별로 어떤 질문을 `rag_search.py`에 던질지 스스로 정함 |
| 3 | RAG (하이브리드 검색·리랭킹·쿼리 확장) | 사용 | `backend/rag/` — 자막·공식 페이지 원문(`g{N}/sources/`)·research.md·발행글을 청크로 나눠 Bedrock Titan 임베딩으로 **Chroma**(`chroma_db/`)에 색인(`rag_index.py`). 검색(`rag_search.py`)은 **쿼리 확장**(LCEL·Claude, 동의어·기관 용어 변형) → **하이브리드**(BM25 한국어 bigram + Chroma 밀집, RRF 융합) → **리랭킹**(Bedrock Rerank, IAM 권한 없으면 Claude 리스트와이즈 자동 대체). writer는 섹션마다, guide-researcher는 절차 항목마다 근거 청크를 검색해 쓰고, `check_faithfulness.py`가 같은 검색기로 초안 주장을 대조 |
| 4 | 도구 다중 (DB·계산기·외부 API) | 사용 | `yt-dlp`(자막 추출), `scripts/svg_to_png.py`(Playwright 래스터화), `scripts/tistory_post.py`(발행), Chroma(벡터 DB), Bedrock Runtime/Agent Runtime API(임베딩·Rerank) 등 서로 다른 외부 도구를 단계별로 조합 호출 |
| 5 | MCP 서버 연동 | 미사용 | `backend/spike/spike1_core.py`에서 커스텀 SDK-MCP 도구(`PresentAngleChoices`)로 앵글선택 대체 가능성만 검증했고, 실제 대화형(Claude Code) 파이프라인에는 아직 적용되지 않음 |
| 6 | 가드레일 (PII·프롬프트 인젝션 방어) | 부분사용 | "영상/근거 문서 밖 사실을 지어내지 않는다", "제작 뒷무대 노출 금지" 같은 **사실성·톤 가드레일**은 각 에이전트 .md에 명시돼 있고, faithfulness 검사가 이를 사후 검증하나, PII 마스킹이나 프롬프트 인젝션 방어 장치는 없음 |
| 7 | HITL (위험 작업 승인) | 사용 | 워크플로우 전체에서 유일한 사용자 확인 지점인 "앵글/키워드 선택"(`AskUserQuestion`, 체크박스 다중 선택). 그 외 단계는 전부 자동 진행 |
| 8 | 미들웨어 (요약·마스킹·재시도) | 부분사용 | reviewer FAIL 시 원인 단계로 되돌려 재실행하는 **재시도 로직**(최대 2회)과 LCEL `with_retry()`, Bedrock 실패 시 단계적 후퇴(확장→원 질문, Rerank→Claude→RRF)는 있으나, 별도의 요약·마스킹 미들웨어 계층은 없음 |
| 9 | Multi-Agent Supervisor | 사용 | 오케스트레이터(메인 에이전트)가 researcher/writer/image/finalizer/reviewer/poster를 순서·병렬 여부까지 직접 지휘하고, 구조화된 판정(`review_vN.json`)의 `issues[].stage`로 재작업 대상 서브에이전트를 라우팅 |
| 10 | Plan-Execute · 장기 메모리 | 사용 | CLAUDE.md에 고정된 워크플로우(Plan)를 앵글마다 그대로 Execute하고, 세션을 넘어 지속되는 auto memory(`MEMORY.md` + 피드백/프로젝트 메모리 파일)로 과거 실수·확정된 정책을 다음 실행에 반영. `chroma_db/`의 발행글 코퍼스도 실행을 넘어 남는 기억(키워드 중복 회피) |
| 11 | Observability · Trace | 사용 | Claude Code hooks(`.claude/settings.json`: SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/PostToolUseFailure/SubagentStart/SubagentStop/Stop/SessionEnd) → `scripts/trace_hook.py`가 `traces/{date}/{session}.jsonl`에 스팬 레코드 기록(실행·앵글·단계 자동 태깅). `scripts/trace_report.py`가 서브에이전트 스팬 duration·앵글×단계 매트릭스·도구 호출 통계·타임라인으로 재구성. 판정 이력은 `review_log.jsonl`에 구조화 누적 |
| 12 | 평가 (RAGAS·LLM-as-Judge) | 사용 | `reviewer`가 **LLM-as-Judge**로 A 양식·B 흡인력·C 문맥 정합성·D 제작 흔적을 0~100 루브릭으로 채점(종합 = 25/35/25/15 가중합, critical 지적 또는 70 미만이면 FAIL — 판정·점수 정합 규칙 명시). 판정은 PASS/FAIL 모두 `review_log.jsonl`에 누적되고 `scripts/eval_report.py`가 최초/최종 PASS율·기준별 평균·원인 단계·반복 지적을 정량 리포트로 집계. 체인이 판정 규칙 위반(PASS인데 critical 등)을 `consistency_warnings`로 잡아냄. **RAGAS faithfulness 류 지표**: `scripts/check_faithfulness.py`가 초안의 사실 주장을 LCEL로 추출하고 RAG 검색으로 근거 청크를 찾아 supported/partially/unsupported를 판정, faithfulness 점수와 `unsupported` 목록을 reviewer 입력(D 기준 critical)으로 넘김 |

**요약**: 12개 중 **사용 9개**(1, 2, 3, 4, 7, 9, 10, 11, 12) · **부분사용 2개**(6, 8) · **미사용
1개**(5). 필수 지정 항목(1, 3, 11, 12)은 모두 사용 중이다.

## 7. 운영 시 알아둘 제약

- **Bedrock**: us-east-1, `.env`의 IAM 키 사용. `bedrock:Rerank` 권한이 없어 Cohere Rerank는 현재
  AccessDenied → Claude 리랭킹으로 자동 대체 중. 일일 토큰 쿼터 스로틀이 간헐적으로 발생하며,
  모든 Bedrock 의존 단계는 실패 시 후퇴 경로가 있다(4절 참조).
- **검증 상태**: LCEL 체인·RAG 검색·faithfulness·eval_report는 실제 Bedrock 호출로 확인했고, hook
  트레이싱은 실제 세션의 도구 이벤트로 확인했다. 서브에이전트 스팬과 end-to-end 파이프라인은
  다음 실제 실행에서 `trace_report.py`·`eval_report.py`로 확인해야 한다.
- **의존성**: `pip install -r requirements.txt` + `python -m playwright install chromium`. Python 3.14.
