# /add-worker-queue

Add or extend the async worker queue for a new message source or job type.

## Context

The existing worker is in `backend/app/worker.py`. It serialises WhatsApp messages
per `chat_id` so concurrent messages from the same conversation are processed in order.
New job types (e.g., scheduled tasks, email digests, self-review runs) follow the same pattern.

## Steps

1. Read `backend/app/worker.py` in full.
2. Identify the new job type and its payload schema.
3. Add a typed `dataclass` or `TypedDict` for the job payload.
4. Add an `enqueue_<job_type>(payload)` helper function.
5. Extend the worker's dispatch loop with a handler for the new type.
6. If the job is periodic, wire it into `backend/app/memory/self_review.py`
   or add a new scheduler in `main.py` lifespan.
7. Add a unit test in `backend/tests/test_worker.py`.

## Constraints

- Worker must remain a single asyncio task (`start_worker` / `stop_worker` pattern).
- Do not use `asyncio.Queue` with `maxsize=0` — always set a bound (default: 100).
- Jobs that fail must log the error and not crash the worker loop.
- Maintain the per-`chat_id` serialisation guarantee for WhatsApp jobs.
