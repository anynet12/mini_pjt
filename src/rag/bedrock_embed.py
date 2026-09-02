"""Bedrock 임베딩(Titan v2)과 Rerank 호출. .env 의 BEDROCK_EMBED_MODEL_ID / BEDROCK_RERANK_MODEL_ID 사용.

Rerank 는 `bedrock:Rerank` IAM 권한이 없으면 AccessDeniedException 이 난다 — 그 경우
retriever 가 Claude 리스트와이즈 리랭킹으로 대체한다 (이 모듈은 실패를 RerankUnavailable 로 알린다).
"""

from __future__ import annotations

import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from src.chains.bedrock import region

EMBED_DIM = int(os.environ.get("BEDROCK_EMBED_DIM", "512"))


class RerankUnavailable(RuntimeError):
    pass


_runtime = None
_agent_runtime = None


def runtime():
    global _runtime
    if _runtime is None:
        _runtime = boto3.client("bedrock-runtime", region_name=region())
    return _runtime


def agent_runtime():
    global _agent_runtime
    if _agent_runtime is None:
        _agent_runtime = boto3.client("bedrock-agent-runtime", region_name=region())
    return _agent_runtime


def embed_model_id() -> str:
    m = os.environ.get("BEDROCK_EMBED_MODEL_ID")
    if not m:
        raise RuntimeError(".env 에 BEDROCK_EMBED_MODEL_ID 가 없습니다")
    return m


def embed_text(text: str) -> list[float]:
    body = {"inputText": text[:8000], "dimensions": EMBED_DIM, "normalize": True}
    r = runtime().invoke_model(modelId=embed_model_id(), body=json.dumps(body))
    return json.loads(r["body"].read())["embedding"]


def embed_texts(texts: list[str]) -> list[list[float]]:
    # Titan v2 는 요청당 1개 텍스트. 규모(자막 1편·공식 페이지 수십 개)가 작아 순차 호출로 충분하다.
    return [embed_text(t) for t in texts]


def bedrock_rerank(query: str, docs: list[str], top_n: int) -> list[tuple[int, float]]:
    """Bedrock Rerank API. 반환: [(doc_index, score)] 관련도 내림차순. 권한/모델 없으면 RerankUnavailable."""
    model = os.environ.get("BEDROCK_RERANK_MODEL_ID")
    if not model:
        raise RerankUnavailable("BEDROCK_RERANK_MODEL_ID 비어 있음")
    arn = f"arn:aws:bedrock:{region()}::foundation-model/{model}"
    try:
        r = agent_runtime().rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[{"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": d[:4000]}}} for d in docs],
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {"modelConfiguration": {"modelArn": arn}, "numberOfResults": min(top_n, len(docs))},
            },
        )
    except (ClientError, BotoCoreError) as e:
        raise RerankUnavailable(str(e)[:200]) from e
    return [(x["index"], float(x["relevanceScore"])) for x in r["results"]]
