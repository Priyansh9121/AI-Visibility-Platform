# Operator runbook

Tasks the operator does by hand on the running service. Each one names the
exact command and what it prints. Started on 2026-09-29.

## Reset someone's password by hand

**When:** a password user asks for a reset. Email is off in production (no
verified sending domain at Resend yet), so the forgot-password screen tells
them to email the operator, "and we'll reset it by hand". This is how.

**Where:** the Render dashboard → the **API** service
(`ai-visibility-platform-5gk4`) → **Shell**. Run:

```bash
cd /app/apps/api && .venv/bin/python scripts/issue_password_reset.py --email 'person@example.com'
```

Put the account's address in the quotes. The shell already has the service's
`DATABASE_URL` and `PUBLIC_WEB_BASE_URL`, so the link points at the live web
app.

**What it prints:**

| Outcome | Exit code | Output |
|---|---|---|
| Link issued | 0 | `Reset link for user_… — it works once and expires in 60 minutes:` and the link on the next line |
| No account with that address | 1 | `No such user.` |
| Signs in with Google only | 2 | Refused: they should use Sign in with Google |
| Suspended, or invited but never joined | 2 | Refused: the account is not active |

It may also print a `password_reset.request.minted` line with the user id.
That is the service's own log line, and it carries no token and no address.

**Then:**

- **Send the link to the account's own address,** by replying to the
  person's email. Never send it to a different address someone gives you:
  whoever holds the link can set the password.
- The link works **once** and expires after **an hour**. Issuing a new one
  cancels any earlier link.
- Opening it asks for a new password. Afterwards the person is signed out
  everywhere and signs in with the new one.

**What it does not do:** it logs nothing that carries the link, the token or
the address. The link appears only in your shell. It mints the token through
the same code as the forgot-password form: the same hashed storage, the same
expiry, and no second way in.

**If the Shell tab is missing** (Render offers no shell on some instance
types), run the same script from a checkout, pointed at production:

```bash
cd apps/api
DATABASE_URL='<the API service's DATABASE_URL, from Render → Environment>' \
PUBLIC_WEB_BASE_URL='https://ai-visibility-platform-1.onrender.com' \
  uv run python scripts/issue_password_reset.py --email 'person@example.com'
```

Both variables are required. Without `PUBLIC_WEB_BASE_URL`, the link points at
`localhost`.
