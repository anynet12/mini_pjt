"""RAG 스택 (패턴 3 · 하이브리드 검색 + 리랭킹 + 쿼리 확장).

- bedrock_embed.py : Titan 임베딩 · Bedrock Rerank 클라이언트 (Rerank 권한 없으면 Claude 대체)
- chunking.py      : 문단·문장 기반 청크 분할, 한국어용 BM25 토크나이저
- store.py         : Chroma(PersistentClient) 벡터 저장소 래퍼 (실행·앵글·종류 메타데이터 필터)
- retriever.py     : 쿼리 확장(LCEL) → BM25 + 밀집 검색 → RRF 융합 → 리랭킹 → top-k
- faithfulness.py  : 초안 주장 추출 → 근거 검색 → 지지 여부 판정 (RAGAS faithfulness 류, 패턴 12)
"""
