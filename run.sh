#!/usr/bin/env bash
# blog-agent 실행 스크립트 (로컬 · Docker 공용)
#
#   ./run.sh video <youtube_url> [--auto] [--blog 블로그명]   영상 트랙 (SDK 실행기)
#   ./run.sh guide [--auto]                                   절차형(가이드) 트랙
#   ./run.sh search "<질문>" --run 2026-09-02 [--kind ...]     하이브리드 RAG 검색
#   ./run.sh index <파일|폴더> --run 2026-09-02 --kind ...      RAG 색인
#   ./run.sh faithfulness <draft_vN.md> --run 2026-09-02        초안 근거 대조
#   ./run.sh review <review_vN.md>                              판정문 구조화 (LCEL)
#   ./run.sh report [posts/{date}]                              품질·트레이스 리포트
#   ./run.sh eval <round> [--init]                              인-아웃 세트 자체 평가 집계
#   ./run.sh check                                              환경 점검 (Bedrock 연결 · 의존성)
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python}
cmd=${1:-help}; shift || true

case "$cmd" in
  video)       exec "$PY" -m src.agent --url "$@" ;;
  guide)       exec "$PY" -m src.agent --guide "$@" ;;
  prompt)      exec "$PY" -m src.agent --prompt "$@" ;;
  search)      exec "$PY" scripts/rag_search.py "$@" ;;
  index)       exec "$PY" scripts/rag_index.py "$@" ;;
  faithfulness) exec "$PY" scripts/check_faithfulness.py "$@" ;;
  review)      exec "$PY" scripts/parse_review.py "$@" ;;
  report)
    run_dir=${1:-}
    if [ -n "$run_dir" ]; then "$PY" scripts/eval_report.py "$run_dir" || true; fi
    "$PY" scripts/trace_report.py || true ;;
  eval)        exec "$PY" evaluation/evaluate.py --round "$@" ;;
  check)
    "$PY" - <<'EOF'
import importlib, os, sys
from pathlib import Path
sys.path.insert(0, ".")
mods = ["boto3", "langchain_aws", "langchain_core", "pydantic", "chromadb", "rank_bm25", "playwright", "yt_dlp", "claude_agent_sdk", "dotenv"]
for m in mods:
    try: importlib.import_module(m); print(f"  ok   {m}")
    except Exception as e: print(f"  MISS {m}: {e}")
from src.chains.bedrock import region, chat_model_id
print("  region:", region(), "| chat:", chat_model_id(), "| embed:", os.environ.get("BEDROCK_EMBED_MODEL_ID"))
import boto3
ident = boto3.client("sts", region_name=region()).get_caller_identity()
print("  aws identity:", ident["Arn"])
print("  chroma_db:", Path(os.environ.get("CHROMA_DB_DIR") or "chroma_db").resolve())
EOF
    ;;
  help|*)
    sed -n '2,13p' "$0" ;;
esac
