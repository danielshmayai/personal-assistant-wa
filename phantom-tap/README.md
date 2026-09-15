# phantom-tap

Precision automation for phone apps with no public API.
Milestone 1: book a Holmes Place studio class the instant registration opens.

> **Staging note.** This tree lives here only as a backup until the standalone
> private repo `danielshmayai/phantom-tap` exists. The GitHub App for this
> session cannot create repositories (`403 Resource not accessible by
> integration`), so the repo has to be created by hand, once, at
> https://github.com/new — name `phantom-tap`, private, no README/gitignore/licence.

See `task_plan.md` for the design and `scripts/device_check.sh` for the
first thing to run.

## Run the device check

On the Mini-PC, with the Android phone attached over USB and USB debugging on:

```bash
bash scripts/device_check.sh
```

It is read-only: it installs nothing and changes no device setting. It answers
one question — whether `com.holmesplace` traffic can be intercepted on this
device — and prints the cheapest path that works if it cannot.
