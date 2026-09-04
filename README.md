# https://padlet.com/ucanlabs/sds-ax-1-aeh67w2kj28h4r3q
# blog-agent — 유튜브 영상 → 티스토리 블로그 자동 집필·발행 에이전트

유튜브 링크 하나(또는 저경쟁 절차형 키워드)에서 검색 노출이 가능한 블로그 글을 여러 편 만들어
품질 게이트를 통과한 것만 티스토리에 발행하는 멀티 에이전트 파이프라인. 사용자 개입은 실행당
1회(앵글/키워드 선택)뿐이다. 서비스 스펙은 [SERVICE.md](SERVICE.md), 동작 규칙의 단일 진실
공급원은 [CLAUDE.md](CLAUDE.md)다.

## 1. 무엇을 하나

```
유튜브 URL ─▶ researcher(자막·앵글) ─▶ [앵글 선택: 유일한 HITL]
        ─▶ writer(RAG 근거 검색) ─▶ image(SVG) ─▶ finalizer(HTML)
        ─▶ check_faithfulness(근거 대조) ─▶ reviewer(A~D 루브릭, PASS/FAIL)
        ─▶ parse_review(LCEL 구조화) ─▶ FAIL: 원인 단계 자동 재작업(≤2회) / PASS: poster 발행
        ─▶ eval_report + trace_report 로 실행 리포트
```

- **두 트랙**: 영상 기반(자막만 근거) · 절차형 가이드(정부·공공기관·금융사 공식 페이지만 근거)
- **근거 한정**: 자막/공식 출처를 Chroma에 색인하고 하이브리드 검색(BM25 + 임베딩 + 리랭킹)으로
  근거 청크를 확인하며 쓴다. 발행 전 주장별 faithfulness 검사로 지어낸 사실을 걸러낸다.
- **품질 게이트**: reviewer가 양식·흡인력·문맥 정합성·제작 흔적을 점수화하고, LCEL 체인이 판정을
  Pydantic 스키마로 구조화해 재작업 라우팅에 쓴다. 판정 이력과 트레이스는 실행별 리포트로 집계된다.

## 2. 구조

```
mini-pjt/
├── CLAUDE.md                 오케스트레이터 규칙 (워크플로우·품질 게이트·파일 구조) — 단일 진실 공급원
├── SERVICE.md                서비스 스펙 (사용자·확장·도구/데이터·정책·성공 기준 + 12 패턴 현황)
├── README.md
├── .claude/
│   ├── agents/*.md           서브에이전트 7종 (researcher · guide-researcher · writer · image · finalizer · reviewer · poster)
│   └── settings.json         hooks → scripts/trace_hook.py (트레이스), askUserQuestionTimeout
├── src/
│   ├── agent.py              메인 에이전트 실행기 (Claude Agent SDK, 비대화형/Docker용, HITL·도구를 MCP로 제공)
│   ├── tools.py              도메인 도구 (자막 추출 · RAG 색인/검색 · faithfulness · 판정 구조화 · PNG · 발행) + SDK-MCP 래퍼
│   ├── retriever.py          RAG 파이프라인 진입점 (파사드)
│   ├── chains/               LCEL 체인 — bedrock.py(모델 팩토리) · review_chain.py(판정 구조화)
│   ├── rag/                  chunking · store(Chroma) · retriever(하이브리드+RRF+리랭킹) · faithfulness · bedrock_embed
│   └── spike/                SDK 검증 실험 (커스텀 MCP HITL · 세션 재개)
├── scripts/                  CLAUDE.md가 Bash로 호출하는 CLI (rag_index · rag_search · check_faithfulness · parse_review
│                             · eval_report · trace_hook · trace_report · cleanup · svg_to_png · tistory_post)
├── data/samples/             재현용 샘플 데이터 (자막 · 공식 페이지 원문 · 초안 · 판정문) — data/README.md 참조
├── evaluation/
│   ├── test_queries.csv      인-아웃 세트 20건 (positive 8 · negative 4 · edge 5 · guardrail 3)
│   ├── evaluate.py           결과 기록(results_roundN.csv) → roundN_report.md 집계
│   ├── round1_report.md      1차 자체 평가 (Day 9)
│   └── round2_report.md      2차 자체 평가 (Day 10 · 개선 후)
├── posts/{date}/             실행 산출물 (a{N}/·g{N}/, 7일 retention) — git 제외
├── chroma_db/ · traces/      RAG 색인 · 트레이스 — git 제외
├── Dockerfile · run.sh · requirements.txt
└── .env                      AWS Bedrock 자격증명·모델 ID — git 제외 (아래 3절)
```

## 3. 설치

요구 사항: Python 3.12+ (개발 환경 3.14), Node 18+ 와 Claude Code CLI(`npm i -g @anthropic-ai/claude-code`),
AWS Bedrock 액세스(Claude · Titan Text Embeddings V2 · 선택적으로 Cohere Rerank).

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # 없으면 아래 항목을 직접 작성
```

`.env`:

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
BEDROCK_CHAT_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
BEDROCK_EMBED_MODEL_ID=amazon.titan-embed-text-v2:0
BEDROCK_RERANK_MODEL_ID=cohere.rerank-v3-5:0     # 권한 없으면 비워도 됨 (Claude 리랭킹으로 대체)
# CHROMA_DB_DIR=chroma_db                         # 선택
# CLAUDE_CODE_USE_BEDROCK=1                        # Claude Code 자체도 Bedrock으로 돌릴 때
```

환경 점검: `./run.sh check` (의존성 import · Bedrock 신원 · 모델 ID 출력).

## 4. 실행

**A. 대화형 (Claude Code)** — 기본 사용법. 프로젝트 루트에서 `claude`를 띄우고:

```
https://www.youtube.com/watch?v=...  이 영상으로 블로그 글 써줘        # 영상 트랙
유튜브 없이 연금 관련 절차형 가이드 글 써줘                               # 가이드 트랙
```

앵글/키워드 선택 질문에 답하면 나머지는 자동 진행되고, 끝나면 발행 URL·리포트를 보고한다.

**B. 스크립트 / Docker** — `src/agent.py`가 같은 CLAUDE.md 규칙을 Claude Agent SDK로 구동한다.
앵글 선택은 콘솔 입력(60초 무응답 또는 `--auto`면 첫 항목)으로 받는다.

```bash
./run.sh video "https://www.youtube.com/watch?v=..." --blog myblog
./run.sh guide --auto
docker build -t blog-agent . && docker run --rm -it --env-file .env -e CLAUDE_CODE_USE_BEDROCK=1 \
  -v "$PWD/posts:/app/posts" -v "$PWD/chroma_db:/app/chroma_db" blog-agent video "https://..."
```

**C. 구성 요소 단독 실행**

```bash
./run.sh index posts/2026-09-02/transcript.txt --run 2026-09-02 --kind transcript
./run.sh search "연금저축 이체 신청은 어디서 하나" --run 2026-09-02 -k 5 --verbose
./run.sh faithfulness posts/2026-09-02/a1/draft_v1.md --run 2026-09-02
./run.sh review posts/2026-09-02/a1/review_v1.md
./run.sh report posts/2026-09-02
```

## 5. 평가

- 인-아웃 세트: [evaluation/test_queries.csv](evaluation/test_queries.csv) — 카테고리 4종, expected_traits /
  forbidden / expected_tools 로 판정.
- 절차: `python evaluation/evaluate.py --round 1 --init`으로 `results_round1.csv`를 만들고, 케이스를 실행하며
  pass(Y/N)·관찰·미충족 항목·금지 항목 출현·호출 도구를 기록한 뒤 `--round 1`로 `round1_report.md`를 생성한다.
- 완성 기준(SERVICE.md §5): 20건 중 17건 이상(85%) 통과, guardrail·negative는 100%.
- 실행별 정량 지표: `scripts/eval_report.py`(PASS율·기준별 평균·반복 지적), `scripts/trace_report.py`
  (앵글×단계 소요·도구 통계), `faithfulness_vN.json`(근거 지지율).

## 6. 현재 상태 · 알려진 제약

- LCEL 체인·RAG 검색·faithfulness·리포트는 실제 Bedrock 호출로 검증했고, hook 트레이싱은 실제 세션
  이벤트로 검증했다. end-to-end 발행과 `src/agent.py` 경로는 다음 실제 실행에서 확인해야 한다.
- `scripts/tistory_post.py`·`tistory_login.py`는 스텁(구현 예정). 발행 전까지는 `post_tistory.html`을
  HTML 모드에 수동 붙여넣기한다.
- Bedrock `bedrock:Rerank` IAM 권한이 없으면 Claude 리랭킹으로 자동 대체되며, 일일 토큰 스로틀 시
  쿼리 확장·리랭킹·구조화가 순서대로 후퇴한다(CLAUDE.md 참조).
- Dockerfile은 로컬에 Docker가 없어 빌드 검증 전이다.
