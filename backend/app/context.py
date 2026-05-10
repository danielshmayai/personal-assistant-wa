from contextvars import ContextVar

# Correlation ID that flows from webhook enqueue → worker → graph run → reply.
# Set once per message in worker._process_one; read by whatsapp and graph modules.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
