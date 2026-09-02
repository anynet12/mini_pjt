# data/ — 사용한 문서·데이터

실제 실행 데이터는 `posts/{date}/`(7일 retention)와 `chroma_db/`(RAG 색인)에 쌓이며 둘 다 git 대상이 아니다.
이 폴더에는 파이프라인 동작을 재현·검증하는 데 쓴 **샘플 데이터**만 둔다 (모두 테스트용으로 만든 가짜 데이터).

```
data/samples/
  transcript.txt               영상 트랙 자막 샘플 (연금저축 계좌이체, 7문장)
  g1/sources/01_fss.md         가이드 트랙 공식 페이지 원문 샘플 (url/title/org/checked 주석 형식)
  g1/draft_v1.md               faithfulness 검사용 초안 — 근거 있는 주장 2건 + 지어낸 주장 2건(10영업일, 수수료 5,000원)
  reviews/review_v1_fail.md    reviewer 판정문 샘플 (새 루브릭 형식 · FAIL · 지적 4건)
  reviews/review_v2_pass.md    reviewer 판정문 샘플 (새 루브릭 형식 · PASS)
  reviews/review_v1_legacy.md  reviewer 판정문 샘플 (옛 형식 · 기준별 점수 없음 → 체인이 추정)
```

재현 절차 (Bedrock 자격증명이 `.env`에 있어야 한다):

```bash
export CHROMA_DB_DIR=/tmp/blog_agent_demo          # 실제 색인과 분리
python scripts/rag_index.py data/samples/transcript.txt --run demo --kind transcript --max-chars 150 --overlap 0
python scripts/rag_index.py data/samples/g1/sources --run demo --angle g1 --kind source --max-chars 160 --overlap 0
python scripts/rag_search.py "이체 신청은 어느 회사에서 하나" --run demo -k 3 --verbose     # 확장·리랭킹 포함
python scripts/check_faithfulness.py data/samples/g1/draft_v1.md --run demo                # unsupported 2건 기대
python scripts/parse_review.py data/samples/reviews/review_v1_fail.md --dry-run            # FAIL/66점/issues 4건 기대
```

실제 데이터 소스(실행 시 수집):
- 유튜브 자막·메타: `python -m yt_dlp` (수동 자막 → 자동 자막)
- 정부·공공기관·금융회사 공식 페이지: guide-researcher 가 WebFetch 로 읽어 `g{N}/sources/`에 저장
- 발행글: poster 단계에서 `post_tistory.html` 을 `kind=post` 로 색인 (다음 실행의 키워드 중복 회피)
