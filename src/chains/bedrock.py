"""Bedrock 모델 팩토리. 프로젝트 루트 .env 를 읽어 ChatBedrockConverse 를 만든다.

필요 환경변수(.env): AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION(또는 AWS_REGION),
BEDROCK_CHAT_MODEL_ID. 선택: BEDROCK_EMBED_MODEL_ID, BEDROCK_RERANK_MODEL_ID (RAG 단계에서 사용).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def region() -> str:
    r = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    if not r:
        raise RuntimeError(".env 에 AWS_DEFAULT_REGION 이 없습니다")
    return r


def chat_model_id() -> str:
    m = os.environ.get("BEDROCK_CHAT_MODEL_ID")
    if not m:
        raise RuntimeError(".env 에 BEDROCK_CHAT_MODEL_ID 가 없습니다")
    return m


def get_chat_model(temperature: float = 0.0, max_tokens: int = 4000):
    """LCEL 파이프에 그대로 꽂을 수 있는 Runnable(ChatBedrockConverse)."""
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=chat_model_id(),
        region_name=region(),
        temperature=temperature,
        max_tokens=max_tokens,
    )
