"""
Verifier node — reviews the agent's completed response and self-corrects
when tool calls failed or the response is incomplete.

Flow:
  agent finishes (no tool_calls)
      → verifier_node
          → passed or max retries → reflection → END
          → failed, retries < 2  → inject correction HumanMessage → agent

Fast path: if the agent made no tool calls at all (pure conversation),
skip the LLM verification entirely — it's unnecessary overhead.
"""

import logging
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from app.graph.state import PAState
from app.llm import get_smart_llm

logger = logging.getLogger("pa.verifier")

_MAX_RETRIES = 2


class VerificationResult(BaseModel):
    passed: bool
    confidence: float           # 0.0 – 1.0
    checks_passed: list[str]
    issues: list[str]           # empty when passed
    correction_prompt: str      # specific fix instruction for the agent


_VERIFIER_PROMPT = """\
You are a strict quality auditor reviewing an AI assistant's response.

Original user request:
{user_input}

Success criteria (from the plan):
{success_criteria}

Assistant's final response:
{agent_reply}

Tool call results (if any):
{tool_results}

Audit ALL of the following:
1. Every distinct part of the user's request is addressed in the response.
2. If tools were required, they were called and returned non-error results \
(a result containing "failed" or "Error" counts as failure).
3. The response concretely satisfies the success_criteria.
4. The response is complete, specific, and not vague or cut off.

Return passed=true ONLY if every check above passes.
If any check fails:
  - List the specific issues
  - Write a correction_prompt that tells the agent exactly what is wrong and what to do differently
  - Set confidence proportional to how many checks passed
"""


async def verifier_node(state: PAState) -> dict:
    # Fast path: agent made no tool calls → pure conversation → skip verification
    tool_msgs_exist = any(isinstance(m, ToolMessage) for m in state["messages"])
    if not tool_msgs_exist:
        logger.info("Verifier: skipping (no tools called)")
        return {"verification_passed": True}

    user_input = state.get("user_input", "")
    success_criteria = (
        state.get("success_criteria")
        or f"Completely and accurately address: {user_input}"
    )
    agent_reply = _extract_last_reply(state["messages"])
    tool_results = _extract_tool_results(state["messages"])

    llm = get_smart_llm().with_structured_output(VerificationResult)
    prompt = _VERIFIER_PROMPT.format(
        user_input=user_input,
        success_criteria=success_criteria,
        agent_reply=agent_reply or "(no reply yet)",
        tool_results=tool_results,
    )

    try:
        result: VerificationResult = await llm.ainvoke([HumanMessage(content=prompt)])
        logger.info(
            "Verifier: passed=%s confidence=%.2f issues=%d",
            result.passed, result.confidence, len(result.issues),
        )
    except Exception:
        logger.exception("Verifier LLM call failed — auto-passing to avoid blocking")
        return {"verification_passed": True}

    if result.passed:
        return {"verification_passed": True}

    attempts = state.get("verification_attempts", 0) + 1
    logger.warning(
        "Verifier: FAILED (attempt %d/%d) issues=%s",
        attempts, _MAX_RETRIES, result.issues,
    )

    correction_msg = HumanMessage(
        content=(
            f"[VERIFICATION FAILED — attempt {attempts}/{_MAX_RETRIES}]\n"
            f"Issues found:\n" + "\n".join(f"• {i}" for i in result.issues) + "\n\n"
            f"Required correction: {result.correction_prompt}"
        )
    )
    return {
        "messages": [correction_msg],
        "verification_attempts": attempts,
        "verification_passed": False,
    }


def route_after_verify(state: PAState) -> str:
    """Conditional edge: pass → reflection, fail+retry → agent, give_up → reflection."""
    if state.get("verification_passed"):
        return "reflection"
    if state.get("verification_attempts", 0) >= _MAX_RETRIES:
        logger.warning("Verifier: max retries reached, proceeding to reflection")
        return "reflection"
    return "agent"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_last_reply(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = msg.content
            if isinstance(content, list):
                return "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            return content or ""
    return ""


def _extract_tool_results(messages: list) -> str:
    results = [
        f"[{msg.tool_call_id}]: {str(msg.content)[:600]}"
        for msg in messages
        if isinstance(msg, ToolMessage)
    ]
    return "\n".join(results) if results else "No tools were called."
