# /review-auth

Review Google OAuth2 and token-storage changes before merging.

## Scope

This command applies to any change touching:
- `backend/app/google/auth.py`
- `backend/app/routers/google_auth.py`
- `backend/app/crypto.py`
- `backend/app/memory/store.py` (token columns)
- `.env.example` (new auth-related vars)

## Checklist

### OAuth flow
- [ ] `state` parameter is generated, stored, and validated (CSRF protection).
- [ ] Token exchange happens server-side only — client never sees the code.
- [ ] Refresh token is persisted; access token is refreshed on expiry, not re-prompted.
- [ ] Redirect URI matches exactly what is registered in Google Cloud Console.

### Token storage
- [ ] Tokens are encrypted with Fernet before `INSERT`/`UPDATE`.
- [ ] Tokens are decrypted after `SELECT` before use.
- [ ] `DB_ENCRYPTION_KEY` rotation path exists (or is noted as a known gap).
- [ ] No token is ever logged (grep for `access_token`, `refresh_token` in log statements).

### Scope hygiene
- [ ] Only the minimum required OAuth scopes are requested.
- [ ] Scope list in `google/auth.py` matches what is listed in `SETUP_GUIDE.md`.

### Error handling
- [ ] Expired / revoked token triggers re-auth prompt to the user, not a 500.
- [ ] `google_connect` tool in `distiller.py` is still reachable via the agent.

## Output

PASS / FAIL per checklist item with file:line references. Flag any FAIL as a blocker.
