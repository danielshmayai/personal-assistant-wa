from typing import Literal
from pydantic import BaseModel, Field
from langgraph.graph import MessagesState


class DistilledIntent(BaseModel):
    """Structured output from the Distiller node."""
    category: Literal[
        "Development_Task",
        "Financial_Log",
        "General_Reminder",
    ] = Field(description="The classified intent of the user's message.")
    is_bug: bool = Field(
        default=False,
        description="True if this is a bug/defect report (only relevant for Development_Task).",
    )
    summary: str = Field(description="A concise summary of the user's request.")
    raw_text: str = Field(default="", description="The original unmodified input.")


class PAState(MessagesState):
    """Full graph state for the Personal Assistant."""
    chat_id: str = ""
    user_input: str = ""
    intent: DistilledIntent | None = None
    memory_context: str = ""
    tool_results: str = ""
    reply: str = ""
