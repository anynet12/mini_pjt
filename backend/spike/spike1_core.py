"""Spike 1: custom-tool angle-selection bridge + system prompt discovery.

Validates (cheaply, no real subagent spawn):
  (a) does the SDK see this repo's CLAUDE.md / .claude/agents when
      setting_sources=["project"] + cwd=repo root?
  (b) can a custom SDK-MCP tool (PresentAngleChoices) stand in for
      AskUserQuestion, with the handler blocking on an asyncio.Future
      until we (simulating the web backend) resolve it?
  (c) what do stream events (include_partial_messages) look like?

Run: python backend/spike/spike1_core.py
"""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    query,
    tool,
    create_sdk_mcp_server,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    SystemMessage,
    ResultMessage,
    StreamEvent,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

pending_answer: asyncio.Future | None = None


@tool(
    "PresentAngleChoices",
    "Present angle/keyword choices to the user and wait for their selection. "
    "Use this instead of AskUserQuestion in web mode.",
    {
        "question": str,
        "options": list,  # [{"label": str, "description": str}]
        "multiSelect": bool,
    },
)
async def present_angle_choices(args: dict) -> dict:
    global pending_answer
    print(f"\n[TOOL CALLED] PresentAngleChoices question={args.get('question')!r} "
          f"options={len(args.get('options', []))} multiSelect={args.get('multiSelect')}")

    loop = asyncio.get_event_loop()
    pending_answer = loop.create_future()

    # simulate the web backend resolving this ~2s later (a real POST /selection)
    async def simulate_web_submit():
        await asyncio.sleep(2)
        print("[SIMULATED WEB SUBMIT] answering with first option")
        if pending_answer and not pending_answer.done():
            pending_answer.set_result(["옵션1"])

    asyncio.create_task(simulate_web_submit())

    selected = await pending_answer
    print(f"[TOOL RESOLVED] selected={selected}")

    return {
        "content": [
            {"type": "text", "text": f"사용자가 선택함: {selected}"}
        ]
    }


async def main():
    server = create_sdk_mcp_server(
        name="blog_agent_web",
        tools=[present_angle_choices],
    )

    options = ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        setting_sources=["project"],
        mcp_servers={"blog_agent_web": server},
        allowed_tools=["mcp__blog_agent_web__PresentAngleChoices"],
        include_partial_messages=True,
        permission_mode="bypassPermissions",
    )

    prompt = (
        "이 프로젝트(cwd)의 CLAUDE.md를 읽었다면, 이 프로젝트에 정의된 서브에이전트 "
        "이름 목록을 한 줄로 알려줘(예: researcher, writer, image, finalizer, reviewer, "
        "poster, guide-researcher). 그다음 mcp__blog_agent_web__PresentAngleChoices 도구를 "
        "question='테스트 질문', options=[{label:'옵션1',description:'첫번째'},"
        "{label:'옵션2',description:'두번째'}], multiSelect=true 로 정확히 한 번 호출해줘. "
        "도구 결과를 받으면 무엇이 선택됐는지 한 줄로 말하고 끝내. 다른 파일은 읽거나 "
        "쓰지 마."
    )

    event_count = 0
    subagent_related_seen = []

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage):
            print(f"[SYSTEM] subtype={message.subtype} data_keys={list(message.data.keys())[:10] if hasattr(message, 'data') else '?'}")
            if hasattr(message, "data"):
                agents = message.data.get("agents") or message.data.get("subagents")
                if agents:
                    print(f"[SYSTEM] agents field: {agents}")
        elif isinstance(message, StreamEvent):
            event_count += 1
            ev = message.event if hasattr(message, "event") else message
            etype = ev.get("type") if isinstance(ev, dict) else getattr(ev, "type", None)
            if etype not in ("content_block_delta",):  # skip noisy token deltas
                print(f"[STREAM #{event_count}] {etype} :: {str(ev)[:200]}")
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"[ASSISTANT TEXT] {block.text}")
                elif isinstance(block, ToolUseBlock):
                    print(f"[ASSISTANT TOOL_USE] name={block.name} input={block.input}")
        elif isinstance(message, ResultMessage):
            print(f"[RESULT] session_id={message.session_id} "
                  f"is_error={message.is_error} "
                  f"num_turns={getattr(message, 'num_turns', '?')} "
                  f"total_cost_usd={getattr(message, 'total_cost_usd', '?')}")

    print(f"\n=== DONE. total stream events (excluding text deltas printed): saw {event_count} events total ===")


if __name__ == "__main__":
    asyncio.run(main())
