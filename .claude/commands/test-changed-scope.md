# /test-changed-scope

Run only the tests that cover files changed since the last commit.

## Steps

1. Get the list of changed files:
   ```bash
   git diff --name-only HEAD
   ```

2. Map changed files to test modules:
   - `backend/app/worker.py` → `backend/tests/test_worker.py`
   - `backend/app/graph/**` → `backend/tests/test_live.py`
   - `backend/app/memory/**` → `backend/tests/test_live.py`
   - `backend/app/google/**` → `backend/tests/test_sanity.py`
   - `backend/app/main.py` → `backend/tests/test_sanity.py`
   - Everything else → `backend/tests/test_sanity.py`

3. Run targeted tests:
   ```bash
   docker compose exec backend pytest <test-files> -v
   ```

4. If any test fails: show the full traceback, identify root cause, suggest fix.

5. After all targeted tests pass, run the full suite as a regression check:
   ```bash
   docker compose exec backend pytest backend/tests/ -v
   ```

## Notes

- `test_live.py` requires real API keys in `.env` — skip if running in CI without secrets.
- Always run `test_sanity.py` — it's fast and catches import/startup regressions.
