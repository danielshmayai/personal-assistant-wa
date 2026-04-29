"""
Planner node — produces a structured AgentPlan before the agent acts.

The plan captures:
  - intent         : one-sentence summary of the user's goal
  - requires_tools : whether API/tool calls will be needed
  - steps          : ordered list of actions
  - success_criteria: measurable statement of "done"

This runs on every turn (fast: no tool binding, focused prompt).
If user_input is empty the node returns a default pass-through plan.
"""

import json
import logging

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.graph.state import PAState
from app.llm import get_smart_llm

logger = logging.getLogger("pa.planner")


class AgentPlan(BaseModel):
    intent: str
    requires_tools: bool
    steps: list[str]
    success_criteria: str


_PLANNER_PROMPT = """\
You are a planning assistant for a personal AI agent.
Analyze the user's request and produce a concise execution plan.

User request: {user_input}
Relevant user context: {memory}

Rules:
- requires_tools = true  → the request needs Gmail, Calendar, Drive, web search, smart home, or memory tools
- requires_tools = false → the request can be answered from knowledge alone (greetings, math, general Q&A)
- success_criteria must be concrete and measurable (e.g. "Email sent to X with subject Y" not "task done")
- steps should be 1-4 short imperative sentences
"""


async def planner_node(state: PAState) -> dict:
    user_input = state.get("user_input", "").strip()

    if not user_input:
        logger.debug("Planner: empty input, using passthrough plan")
        plan = AgentPlan(
            intent="No input provided",
            requires_tools=False,
            steps=["Reply with a prompt to provide a request"],
            success_criteria="User receives a helpful prompt",
        )
        return _plan_to_state(plan)

    llm = get_smart_llm().with_structured_output(AgentPlan)
    memory = state.get("memory_context", "") or "None available"
    prompt = _PLANNER_PROMPT.format(user_input=user_input, memory=memory)

    try:
        plan: AgentPlan = await llm.ainvoke([HumanMessage(content=prompt)])
        logger.info(
            "Planner: intent=%r requires_tools=%s steps=%d",
            plan.intent, plan.requires_tools, len(plan.steps),
        )
    except Exception:
        logger.exception("Planner LLM call failed — using fallback plan")
        plan = AgentPlan(
            intent=user_input[:120],
            requires_tools=True,
            steps=["Attempt to fulfil the request directly"],
            success_criteria="User receives a useful response",
        )

    return _plan_to_state(plan)


def _plan_to_state(plan: AgentPlan) -> dict:
    return {
        "agent_plan": plan.model_dump_json(),
        "success_criteria": plan.success_criteria,
        "verification_attempts": 0,
        "verification_passed": False,
    }
