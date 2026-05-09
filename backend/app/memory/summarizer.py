"""Rolling conversation summarization — background task fired after each graph turn."""

import asyncio
import logging
from langchain_core.messages import HumanMessage, AIMessage

logger = logging.getLogger("pa.summarizer")

_MIN_MESSAGES = 15  # Don't summarize short conversations

_INITIAL_PROMPT = """\
Summarize this conversation in 3-5 sentences. Cover:
1. Main topics and requests from the user
2. Key facts the user mentioned about themselves or their situation
3. What was resolved and any unresolved items

{messages}

Return only the summary text, no preamble."""

_UPDATE_PROMPT = """\
You are maintaining a rolling summary of an ongoing conversation.

Existing summary (covers everything before the new messages):
{existing_summary}

New messages to incorporate:
{new_messages}

Update the summary to incorporate the new messages. Keep it concise (3-5 sentences). Cover:
1. Main topics / requests across the whole conversation
2. Key facts or decisions made
3. Any unresolved items or context the assistant should remember

Return only the updated summary text, no preamble."""


def _format_messages(messages: list) -> str:
    parts = []
    for m in messages:
        if isinstance(m, HumanMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            parts.append(f"User: {content}")
        elif isinstance(m, AIMessage):
            content = m.content
            if isinstance(content, list):
                content = "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in content
                )
            parts.append(f"Assistant: {content}")
    return "\n".join(parts)


async def summarize_conversation(chat_id: str) -> None:
    """Background task: fetch messages from the checkpointer and update the rolling summary.

    Only runs when the conversation has more than _MIN_MESSAGES messages. Updates
    incrementally — summarizes "existing summary + new messages" rather than the full history.
    """
    try:
        from app.graph.checkpointer import get_checkpointer
        from app.llm import get_smart_llm
        from app.memory.store import get_conversation_summary, upsert_conversation_summary

        cp = get_checkpointer()
        tup = await cp.aget_tuple({"configurable": {"thread_id": chat_id}})
        if not tup:
            return

        all_messages = tup.checkpoint.get("channel_values", {}).get("messages", [])
        # Only count Human + non-tool-call AI messages (the actual conversation)
        conversation_msgs = [
            m for m in all_messages
            if isinstance(m, (HumanMessage, AIMessage))
            and not (isinstance(m, AIMessage) and getattr(m, "tool_calls", None))
        ]

        if len(conversation_msgs) <= _MIN_MESSAGES:
            return

        loop = asyncio.get_running_loop()
        existing = await loop.run_in_executor(None, get_conversation_summary, chat_id)

        if existing:
            prev_summary, prev_count = existing
            new_msgs = conversation_msgs[prev_count:]
            if not new_msgs:
                return  # Nothing new to incorporate
            prompt = _UPDATE_PROMPT.format(
                existing_summary=prev_summary,
                new_messages=_format_messages(new_msgs),
            )
        else:
            new_msgs = conversation_msgs
            prompt = _INITIAL_PROMPT.format(messages=_format_messages(new_msgs))

        llm = get_smart_llm()
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        summary = (response.content or "").strip()
        if not summary:
            return

        await loop.run_in_executor(
            None, upsert_conversation_summary, chat_id, summary, len(conversation_msgs)
        )
        logger.debug(
            "Conversation summary updated for chat_id=%s (%d msgs)", chat_id, len(conversation_msgs)
        )

    except Exception:
        logger.debug("summarize_conversation failed for chat_id=%s", chat_id, exc_info=True)
