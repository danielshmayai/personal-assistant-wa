# /harden-assistant

Audit and tighten the security posture of the PA backend.

## Checks to perform

### 1. CORS
- Read `backend/app/main.py` CORS setup.
- Confirm `ALLOWED_ORIGIN` is the only origin; no wildcard (`*`) is present.
- Confirm `allow_credentials=True` is not combined with a wildcard origin.

### 2. Webhook authentication
- Read `backend/app/whatsapp.py`.
- Confirm every incoming webhook request validates `WEBHOOK_SECRET`.
- Confirm the secret comparison is constant-time (use `hmac.compare_digest`, not `==`).

### 3. Token storage
- Read `backend/app/crypto.py` and `backend/app/google/auth.py`.
- Confirm Google tokens are Fernet-encrypted before being written to PostgreSQL.
- Confirm `DB_ENCRYPTION_KEY` is required and the app refuses to start (or logs CRITICAL) when it is unset.

### 4. Endpoint protection
- Grep for `@app.post`, `@app.get`, `@app.delete` in `backend/app/`.
- List every endpoint and its auth mechanism.
- Flag any endpoint reachable without a token / secret.

### 5. Rate limiting
- Confirm `slowapi` limiter is attached to all public-facing endpoints.
- Flag any endpoint missing `@limiter.limit(...)`.

### 6. Dependency audit
```bash
pip-audit -r backend/requirements.txt
```

## Output

For each check: **PASS**, **WARN**, or **FAIL** with a one-line explanation and the file:line reference.
End with a prioritised fix list.
