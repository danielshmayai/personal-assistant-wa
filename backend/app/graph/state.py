from langgraph.graph import MessagesState


class PAState(MessagesState):
    """Full graph state for the Personal Assistant."""
    chat_id: str = ""
    user_input: str = ""
    memory_context: str = ""
    reply: str = ""
    # Multi-tenant product fields ('' / None = legacy owner path, all modules)
    tenant_id: str = ""
    enabled_modules: list[str] | None = None
