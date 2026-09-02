# 서비스 구조 (blog-agent)

> CLAUDE.md가 정의하는 오케스트레이션 규칙의 "구조 요약 + LLM 에이전트 패턴 관점 점검" 문서.
> 실제 동작 규칙의 단일 진실 공급원은 여전히 CLAUDE.md이며, 이 문서는 그 위에 아키텍처
> 관점의 조감도를 얹은 것이다.

## 1. 트랙 개요

두 개의 입력 트랙이 하나의 파이프라인(writer→image→finalizer→reviewer→poster)을 공유한다.

- **영상 기반 트랙**: 유튜브 URL → `researcher`(자막 추출·인사이트·앵글 도출)
- **절차형(가이드) 트랙**: 저경쟁 How-to 키워드 → `guide-researcher`(키워드 발굴·공식 출처 리서치, 유일하게 웹 검색 허용)

## 2. 서브에이전트 구성 (Multi-Agent Supervisor)

```
오케스트레이터(메인 에이전트)
  ├─ researcher / guide-researcher   (자료 수집)
  ├─ writer         (초안 작성)
  ├─ image          (SVG 이미지 제작)
  ├─ finalizer      (퇴고 + HTML 빌드)
  ├─ reviewer       (품질 게이트, PASS/FAIL 판정만)
  └─ poster         (티스토리 자동 발행)
```

오케스트레이터가 각 서브에이전트를 호출·감독하며, 앵글(또는 키워드)이 N개면 N개
파이프라인을 **앵글별 독립 병렬**로 진행한다(단계별 일괄 동기화 없음). 진행 상태는
앵글×단계 매트릭스로 매 호출/완료/재작업마다 다시 보고된다.

## 3. 실행 단위 · 파일 구조

- `posts/{date}/` — 실행 단위. `transcript.txt`/`insights.md`(영상) 또는
  `keyword_candidates.md`(가이드)를 공유 자료로 둔다.
- `posts/{date}/a{N}/` 또는 `g{N}/` — 앵글/키워드별 산출물 세트
  (`draft_vN.md` → `images_vN/` → `post_vN.html` → `post_tistory.html`/`post_naver.html`
  → `review_log.md`). 버전은 단계별 독립, "현재본" = 가장 큰 번호.

## 4. 품질 게이트 · 재작업

finalizer 직후 `check_faithfulness.py`(RAG 근거 대조) → `reviewer`가 A~D 루브릭 점수와 PASS/FAIL 판정(직접 수정 안 함) → `parse_review.py`(LCEL)가 판정을 구조화 → FAIL 시 오케스트레이터가 `issues[].stage`로 원인 단계에
자동 재작업 지시(최대 2회) → 매 판정을 `review_log.md/.jsonl`에 기록 → 2회 초과 시 해당
앵글은 drop(발행 제외) → 전체 실행 종료 후 `eval_report.py`·`trace_report.py`로 정량 지표와 반복 패턴을
분석해 개선 방향을 1회 보고.

## 5. 발행

`poster`가 `scripts/tistory_post.py`로 `post_tistory.html`(텍스트+형광펜+태그)을 자동
발행. 이미지는 자동 발행 경로에 못 들어가 사람이 HTML 모드로 수동 붙여넣기 + 대표이미지
수동 등록. 네이버는 `post_naver.html`(이미지 자리표시자)을 사람이 반자동으로 옮김.

## 6. 12개 LLM 에이전트 패턴 사용 현황

| # | 항목 | 사용 여부 | 비고 (사용 사례 / 미사용·제한 사유) |
|---|---|---|---|
| 1 | LCEL chain (Pydantic 구조화 출력) | 사용 | `backend/chains/review_chain.py` — reviewer 판정문을 `RunnablePassthrough.assign(정규식 추출) \| prompt \| ChatBedrockConverse.with_structured_output(ReviewVerdict).with_retry() \| reconcile` LCEL 파이프로 Pydantic 스키마(`verdict`·`scores{A,B,C,D,total}`·`issues[{criterion,severity,stage,…}]`)에 구조화. 오케스트레이터가 매 심사마다 `scripts/parse_review.py`로 호출하고 `issues[].stage`로 재작업을 라우팅 |
| 2 | ReAct (도구 자율 선택) | 사용 | 각 서브에이전트가 부여된 도구 집합 안에서 상황에 맞게 자율 선택 — 예: `researcher`가 자막 추출에 `Bash`(yt-dlp)를, 앵글 정리에 `Write`를 스스로 판단해 사용 |
| 3 | RAG (하이브리드 검색·리랭킹·쿼리 확장) | 사용 | `backend/rag/` — 자막·공식 페이지 원문(`g{N}/sources/`)·research.md·발행글을 청크로 나눠 Bedrock Titan 임베딩으로 **Chroma**(`chroma_db/`)에 색인(`rag_index.py`). 검색(`rag_search.py`)은 **쿼리 확장**(LCEL·Claude, 동의어·기관 용어 변형) → **하이브리드**(BM25 한국어 bigram + Chroma 밀집, RRF 융합) → **리랭킹**(Bedrock Rerank, IAM 권한 없으면 Claude 리스트와이즈 자동 대체). writer는 섹션마다, guide-researcher는 절차 항목마다 근거 청크를 검색해 쓰고, `check_faithfulness.py`가 같은 검색기로 초안 주장을 대조 |
| 4 | 도구 다중 (DB·계산기·외부 API) | 사용 | `yt-dlp`(자막 추출), `scripts/svg_to_png.py`(Playwright 래스터화), `scripts/tistory_post.py`(발행) 등 서로 다른 외부 도구를 단계별로 조합 호출 |
| 5 | MCP 서버 연동 | 미사용 | `backend/spike/spike1_core.py`에서 커스텀 SDK-MCP 도구(`PresentAngleChoices`)로 앵글선택 대체 가능성만 검증했고, 실제 대화형(Claude Code) 파이프라인에는 아직 적용되지 않음 |
| 6 | 가드레일 (PII·프롬프트 인젝션 방어) | 부분사용 | "영상/근거 문서 밖 사실을 지어내지 않는다", "제작 뒷무대 노출 금지" 같은 **사실성·톤 가드레일**은 각 에이전트 .md에 명시돼 있으나, PII 마스킹이나 프롬프트 인젝션 방어 장치는 없음 |
| 7 | HITL (위험 작업 승인) | 사용 | 워크플로우 전체에서 유일한 사용자 확인 지점인 "앵글/키워드 선택"(`AskUserQuestion`, 체크박스 다중 선택). 그 외 단계는 전부 자동 진행 |
| 8 | 미들웨어 (요약·마스킹·재시도) | 부분사용 | reviewer FAIL 시 원인 단계로 되돌려 재실행하는 **재시도 로직**(최대 2회)은 있으나, 별도의 요약·마스킹 미들웨어 계층은 없음 |
| 9 | Multi-Agent Supervisor | 사용 | 오케스트레이터(메인 에이전트)가 researcher/writer/image/finalizer/reviewer/poster를 순서·병렬 여부까지 직접 지휘하고, reviewer 피드백을 해석해 재작업 대상 서브에이전트를 라우팅 |
| 10 | Plan-Execute · 장기 메모리 | 사용 | CLAUDE.md에 고정된 워크플로우(Plan)를 앵글마다 그대로 Execute하고, 세션을 넘어 지속되는 auto memory(`MEMORY.md` + 피드백/프로젝트 메모리 파일)로 과거 실수·확정된 정책을 다음 실행에 반영 |
| 11 | Observability · Trace | 사용 | Claude Code hooks(`.claude/settings.json`: SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/PostToolUseFailure/SubagentStart/SubagentStop/Stop/SessionEnd) → `scripts/trace_hook.py`가 `traces/{date}/{session}.jsonl`에 스팬 레코드 기록(실행·앵글·단계 자동 태깅). `scripts/trace_report.py`가 서브에이전트 스팬 duration·앵글×단계 매트릭스·도구 호출 통계·타임라인으로 재구성. 판정 이력은 `review_log.jsonl`에 구조화 누적 |
| 12 | 평가 (RAGAS·LLM-as-Judge) | 사용 | `reviewer`가 **LLM-as-Judge**로 A 양식·B 흡인력·C 문맥 정합성·D 제작 흔적을 0~100 루브릭으로 채점(종합 = 25/35/25/15 가중합, critical 지적 또는 70 미만이면 FAIL — 판정·점수 정합 규칙 명시). 판정은 PASS/FAIL 모두 `review_log.jsonl`에 누적되고 `scripts/eval_report.py`가 최초/최종 PASS율·기준별 평균·원인 단계·반복 지적을 정량 리포트로 집계. 체인이 판정 규칙 위반(PASS인데 critical 등)을 `consistency_warnings`로 잡아냄. **RAGAS faithfulness 류 지표**: `scripts/check_faithfulness.py`가 초안의 사실 주장을 LCEL로 추출하고 RAG 검색으로 근거 청크를 찾아 supported/partially/unsupported를 판정, faithfulness 점수와 `unsupported` 목록을 reviewer 입력(D 기준 critical)으로 넘김 |

**요약**: 12개 중 **사용 9개**(1, 2, 3, 4, 7, 9, 10, 11, 12) · **부분사용 2개**(6, 8) · **미사용
1개**(5). 필수 지정 항목(1, 3, 11, 12)은 모두 사용 중이다.
