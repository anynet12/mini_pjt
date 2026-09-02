# blog-agent 클린 환경 재현
# - Playwright 공식 이미지(Python + Chromium 포함) 위에 Node(Claude Code CLI)와 프로젝트 의존성을 얹는다.
# - LLM 은 AWS Bedrock 을 쓴다: 실행 시 .env(또는 -e AWS_*)를 넘긴다. 이미지에 키를 굽지 않는다.
#
#   docker build -t blog-agent .
#   docker run --rm -it --env-file .env -e CLAUDE_CODE_USE_BEDROCK=1 \
#       -v "$PWD/posts:/app/posts" -v "$PWD/chroma_db:/app/chroma_db" -v "$PWD/traces:/app/traces" \
#       blog-agent --url "https://www.youtube.com/watch?v=..."
#   docker run --rm -it --env-file .env blog-agent search "연금저축 이체 방법" --run 2026-09-02
#
# 주의: 로컬에 Docker 가 없어 이 파일은 아직 빌드로 검증되지 않았다. playwright 태그는 requirements 의
# playwright 버전과 맞춰야 한다 (pip 로 다른 버전이 깔리면 브라우저를 다시 받는다).

FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8

# Node 22 + Claude Code CLI (서브에이전트 오케스트레이션 런타임)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && python -m playwright install chromium

COPY . .

# 런타임 산출물 디렉터리 (볼륨으로 덮어써도 됨)
RUN mkdir -p posts chroma_db traces && chmod +x run.sh

ENTRYPOINT ["./run.sh"]
CMD ["help"]
