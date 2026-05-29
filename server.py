"""ChatGPT MCP 서버 (FastMCP, stdio).

사람이 로그인해 둔 실제 Chrome(CDP 9223)에 붙어 ChatGPT 를 조작/조회하는 도구 묶음.
동기 Playwright 코어(chatgpt_client)를 asyncio.to_thread 로 호출해 이벤트 루프 충돌을 피한다.

전제: ChatGPT 에 로그인된 Chrome 이 --remote-debugging-port=9223 으로 떠 있어야 함.
      (포트는 환경변수 CHATGPT_CDP_PORT 로 변경 가능)

실행: python server.py        (stdio MCP 서버)
등록: claude mcp add chatgpt -- python "C:/Users/kimghw/web_chatgpt/server.py"
"""
from __future__ import annotations

import asyncio
from typing import Optional

from mcp.server.fastmcp import FastMCP

import chatgpt_client as cc

mcp = FastMCP(
    "chatgpt",
    instructions=(
        "사람이 로그인한 Chrome(CDP)에 붙어 ChatGPT 를 다룬다. "
        "먼저 chatgpt_session_status 로 로그인 여부를 확인하라. "
        "조회는 자유지만 chatgpt_ask/ask_in_conversation 은 계정에 대화를 '쓰는' 동작이니 필요할 때만 쓴다."
    ),
)


@mcp.tool()
async def chatgpt_session_status() -> dict:
    """ChatGPT 로그인/세션 상태를 확인한다.

    Returns: {logged_in: bool, email: str|None, expires: str|None}
    logged_in 이 false 면 사용자가 그 Chrome 창에서 직접 로그인해야 한다.
    """
    return await asyncio.to_thread(cc.check_session)


@mcp.tool()
async def chatgpt_list_conversations(limit: int = 50, include_archived: bool = False) -> dict:
    """대화(채팅) 목록을 최신 업데이트순으로 가져온다.

    Args:
        limit: 최대 개수 (0 이면 전체 — 수천 개일 수 있으니 주의).
        include_archived: 보관(archived)된 대화 포함 여부.
    Returns: {ok, total, count, items:[{id, title, create_time, update_time, is_archived}]}
    items[].id 를 chatgpt_get_conversation / ask_in_conversation 에 쓴다.
    """
    return await asyncio.to_thread(cc.list_conversations, (limit or None), include_archived)


@mcp.tool()
async def chatgpt_get_conversation(conversation_id: str = "") -> dict:
    """특정 대화의 전체 메시지를 가져온다.

    Args:
        conversation_id: 대화 id. 빈 문자열이면 현재 활성 탭에 열린 대화(/c/<id>).
    Returns: {ok, title, model, conversation_id, msgs:[{role, text, create_time}]}
    (msgs[].text 는 ChatGPT 내부 마커가 제거된 읽기용 텍스트)
    """
    return await asyncio.to_thread(cc.get_conversation, (conversation_id or None), True)


@mcp.tool()
async def chatgpt_ask(prompt: str, wait_timeout: float = 150.0) -> dict:
    """새 채팅을 열어 질문을 보내고 답변을 회수한다 (모델은 계정 기본값).

    Args:
        prompt: 보낼 질문/지시.
        wait_timeout: 응답 완료 대기 최대 초.
    Returns: {ok, conversation_id, prompt, answer, title, model, total_messages}
    주의: 계정에 새 대화가 생성된다(쓰기 동작).
    """
    return await asyncio.to_thread(cc.ask, prompt, wait_timeout, True)


@mcp.tool()
async def chatgpt_ask_in_conversation(conversation_id: str, prompt: str, wait_timeout: float = 150.0) -> dict:
    """기존 대화에 이어서 질문을 보내고 답변을 회수한다 (이전 맥락 유지).

    Args:
        conversation_id: 이어쓸 대화 id (chatgpt_list_conversations 의 items[].id).
        prompt: 후속 질문/지시.
        wait_timeout: 응답 완료 대기 최대 초.
    Returns: {ok, conversation_id, prompt, answer, assistant_before_after, total_messages, ...}
    """
    return await asyncio.to_thread(cc.ask_in_conversation, conversation_id, prompt, wait_timeout, True)


if __name__ == "__main__":
    mcp.run()  # 기본 stdio 트랜스포트
