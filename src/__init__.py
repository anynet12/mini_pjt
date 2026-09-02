"""blog-agent 소스 패키지.

- agent.py      : 메인 에이전트 실행기 (Claude Agent SDK로 CLAUDE.md 오케스트레이션을 구동)
- tools.py      : 도메인 도구 (자막 추출·RAG 색인/검색·faithfulness·판정 구조화·PNG 변환) + SDK-MCP 래퍼
- retriever.py  : RAG 파이프라인 진입점 (하이브리드 검색 파사드)
- chains/       : LCEL 체인 (Bedrock + Pydantic 구조화 출력)
- rag/          : 청크·임베딩·Chroma 저장소·하이브리드 검색기·faithfulness
- spike/        : SDK 검증용 실험 스크립트
"""
