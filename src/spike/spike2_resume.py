"""Spike 2: session resume preserves context across separate process runs.

Run: python src/spike/spike2_resume.py
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query, AssistantMessage, TextBlock, ResultMessage

REPO_ROOT = Path(__file__).resolve().parents[2]


async def run_first():
    options = ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        setting_sources=["project"],
        permission_mode="bypassPermissions",
    )
    session_id = None
    async for message in query(
        prompt="이 숫자를 기억해: 728491. 다른 건 하지 말고 '기억했다'고만 답해.",
        options=options,
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"[RUN1 ASSISTANT] {block.text}")
        if isinstance(message, ResultMessage):
            session_id = message.session_id
            print(f"[RUN1 RESULT] session_id={session_id}")
    return session_id


async def run_resume(session_id: str):
    options = ClaudeAgentOptions(
        cwd=str(REPO_ROOT),
        setting_sources=["project"],
        permission_mode="bypassPermissions",
        resume=session_id,
    )
    async for message in query(
        prompt="내가 방금 기억하라고 한 숫자가 뭐였지? 숫자만 답해.",
        options=options,
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"[RUN2 ASSISTANT] {block.text}")
        if isinstance(message, ResultMessage):
            print(f"[RUN2 RESULT] session_id={message.session_id} is_error={message.is_error}")


async def main():
    print("=== RUN 1 (fresh session) ===")
    sid = await run_first()
    print(f"\n=== waiting 2s to simulate separate process/time gap ===\n")
    await asyncio.sleep(2)
    print(f"=== RUN 2 (resume session_id={sid}) ===")
    await run_resume(sid)


if __name__ == "__main__":
    asyncio.run(main())
