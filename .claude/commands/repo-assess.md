# /repo-assess

Produce a concise health report of the `pa` repository before a large change.

## Steps

1. Read `backend/app/config.py` — list every env var and note which ones have no default (i.e., security-critical).
2. Read `docker-compose.yml` — identify services, port exposures, and volumes.
3. Run `git log --oneline -20` — summarise the last 20 commits in one sentence each.
4. Grep for `TODO|FIXME|HACK|XXX` across `backend/` — list findings with file:line.
5. Check `backend/requirements.txt` for any packages pinned to an exact version that has a known security advisory (use your training knowledge; flag anything obviously outdated).
6. Report open issues:
   - Missing required env vars (no default in config.py)
   - Exposed ports that shouldn't be public
   - TODO/FIXME debt
   - Obvious dependency risks

## Output format

```
## PA Repo Health Report

### Env Var Coverage
...

### Docker Surface
...

### Recent Commits
...

### Code Debt
...

### Dependency Flags
...

### Recommended Actions (priority order)
1. ...
```
