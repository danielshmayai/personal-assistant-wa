# /safe-refactor

Refactor a file or module without changing observable behaviour.

## Usage

```
/safe-refactor <file-or-module>
```

## Steps

1. Read the target file(s) in full.
2. Run the relevant tests to establish a baseline:
   ```bash
   docker compose exec backend pytest backend/tests/ -v -k "<relevant test>"
   ```
3. Identify refactoring opportunities (dead code, duplication, unclear names, missing type hints).
4. Apply changes — **one logical change at a time**, not a big-bang rewrite.
5. After each change, run the test suite again to confirm green.
6. If any test fails, revert the last change and explain why.
7. Summarise: what changed, what didn't, and what you left alone (with reason).

## Constraints

- Do not add new features or fix bugs discovered along the way (open a task instead).
- Do not change public function signatures without updating all call sites.
- Do not modify `backend/app/config.py` — env var names are part of the external interface.
- Keep `backend/app/graph/graph.py` node names unchanged — they appear in LangSmith traces.
