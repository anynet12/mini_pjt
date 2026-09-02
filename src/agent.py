"""메인 에이전트 실행기 — Claude Agent SDK 로 CLAUDE.md 오케스트레이션을 구동한다.

이 프로젝트의 "에이전트 그래프" 는 CLAUDE.md(오케스트레이터 규칙) + .claude/agents/*.md(서브에이전트) 로
정의돼 있고, 대화형 Claude Code 에서 그대로 돈다. 이 스크립트는 같은 정의를 **비대화형/스크립트 환경**
(run.sh, Docker, 웹 백엔드) 에서 실행하기 위한 진입점이다.

- setting_sources=["project"] 로 CLAUDE.md·서브에이전트·hooks(트레이스) 를 그대로 로드한다.
- 앵글/키워드 선택(유일한 HITL)은 `AskUserQuestion` 대신 커스텀 MCP 도구 `PresentAngleChoices` 로 받는다:
  콘솔이 있으면 사용자 입력, `--auto` 또는 무입력(타임아웃)이면 첫 번째 항목으로 진행(CLAUDE.md 기본값과 동일).
- 도메인 도구(rag_search/rag_index/check_faithfulness/parse_review)를 in-process MCP 서버로도 노출한다(src/tools.py).

사용법:
    python -m src.agent --url https://www.youtube.com/watch?v=...      # 영상 트랙
    python -m src.agent --guide                                        # 절차형(가이드) 트랙
    python -m src.agent --prompt "자유 지시"                             # 임의 프롬프트
    옵션: --auto (앵글 자동 선택) --timeout 60 --blog {티스토리명} --max-turns 200

Claude Code 자체를 Bedrock 으로 돌리려면 .env 에 CLAUDE_CODE_USE_BEDROCK=1 을 두면 된다(AWS 키는 이미 .env 에 있음).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from src.tools import MCP_TOOL_NAMES, build_mcp_server  # noqa: E402

AUTO_SELECT = False
SELECT_TIMEOUT = 60


async def _read_console(prompt: str, timeout: int) -> str | None:
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, lambda: input(prompt)), timeout=timeout)
    except (asyncio.TimeoutError, EOFError):
        return None


@tool(
    "PresentAngleChoices",
    "앵글/키워드 후보를 사용자에게 제시하고 선택을 기다린다. 이 실행에서 AskUserQuestion 대신 반드시 이 도구를 쓴다. "
    "options 는 [{label, description}] 목록, multiSelect 는 true.",
    {"question": str, "options": list, "multiSelect": bool},
)
async def present_angle_choices(args: dict) -> dict:
    options = args.get("options") or []
    labels = [o.get("label") if isinstance(o, dict) else str(o) for o in options]
    print("\n" + "=" * 70)
    print(f"[선택 요청] {args.get('question')}")
    for i, o in enumerate(options, 1):
        desc = o.get("description", "") if isinstance(o, dict) else ""
        print(f"  {i}. {labels[i-1]}" + (f" — {desc}" if desc else ""))
    print("=" * 70)

    selected: list[str] = []
    if not AUTO_SELECT and sys.stdin.isatty():
        raw = await _read_console(f"번호를 쉼표로 구분해 입력 ({SELECT_TIMEOUT}초 무응답 시 1번): ", SELECT_TIMEOUT)
        if raw:
            for tok in raw.replace(" ", "").split(","):
                if tok.isdigit() and 1 <= int(tok) <= len(labels):
                    selected.append(labels[int(tok) - 1])
    if not selected and labels:
        selected = [labels[0]]
        print(f"[기본값] 첫 번째 항목으로 진행: {selected[0]}")
    else:
        print(f"[선택] {selected}")
    return {"content": [{"type": "text", "text": f"사용자가 선택함: {selected}"}]}


def build_prompt(args: argparse.Namespace) -> str:
    hitl = ("앵글/키워드 선택 단계에서는 AskUserQuestion 대신 mcp__blog_agent_hitl__PresentAngleChoices 도구를 "
            "options=[{label, description}], multiSelect=true 로 호출해 사용자 선택을 받는다. ")
    blog = f"티스토리 블로그명은 '{args.blog}'다. " if args.blog else ""
    if args.prompt:
        return hitl + args.prompt
    if args.url:
        return hitl + blog + f"다음 유튜브 영상으로 블로그 글을 작성해 발행까지 진행해줘: {args.url}"
    return hitl + blog + "유튜브 링크 없이 절차형(가이드) 트랙으로 저경쟁 How-to 키워드를 발굴해 글을 작성해줘."


async def run(args: argparse.Namespace) -> int:
    hitl_server = create_sdk_mcp_server(name="blog_agent_hitl", tools=[present_angle_choices])
    tools_server = build_mcp_server("blog_agent")
    env = {k: v for k, v in os.environ.items() if k.startswith(("AWS_", "CLAUDE_CODE_", "ANTHROPIC_"))}
    options = ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        setting_sources=["project"],
        mcp_servers={"blog_agent_hitl": hitl_server, "blog_agent": tools_server},
        allowed_tools=["mcp__blog_agent_hitl__PresentAngleChoices", *MCP_TOOL_NAMES],
        permission_mode="bypassPermissions",
        max_turns=args.max_turns,
        env=env or None,
    )
    prompt = build_prompt(args)
    print(f"[agent] cwd={REPO_ROOT}\n[agent] prompt={prompt[:160]}…\n")
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage):
            if getattr(message, "subtype", "") == "init":
                print(f"[system] session={message.data.get('session_id')} model={message.data.get('model')}")
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    print(block.text)
                elif isinstance(block, ToolUseBlock):
                    brief = {k: (str(v)[:80]) for k, v in (block.input or {}).items() if k in ("subagent_type", "description", "command", "file_path", "query")}
                    print(f"  ▸ {block.name} {brief}")
        elif isinstance(message, ResultMessage):
            print(f"\n[result] turns={getattr(message, 'num_turns', '?')} cost=${getattr(message, 'total_cost_usd', 0) or 0:.4f} "
                  f"error={message.is_error} session={message.session_id}")
            return 1 if message.is_error else 0
    return 0


def main() -> None:
    global AUTO_SELECT, SELECT_TIMEOUT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--url", help="유튜브 URL (영상 트랙)")
    g.add_argument("--guide", action="store_true", help="절차형(가이드) 트랙")
    g.add_argument("--prompt", help="임의 프롬프트")
    ap.add_argument("--auto", action="store_true", help="앵글/키워드를 묻지 않고 첫 항목으로 진행")
    ap.add_argument("--timeout", type=int, default=60, help="선택 대기 초 (기본 60)")
    ap.add_argument("--blog", default=os.environ.get("TISTORY_BLOG"), help="티스토리 블로그명")
    ap.add_argument("--max-turns", type=int, default=300)
    args = ap.parse_args()
    if not (args.url or args.guide or args.prompt):
        ap.error("--url, --guide, --prompt 중 하나를 지정하세요")
    AUTO_SELECT = args.auto
    SELECT_TIMEOUT = args.timeout
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
