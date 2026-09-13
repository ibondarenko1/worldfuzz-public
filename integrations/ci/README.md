# CI, embedded results and local signed events

Use the existing authenticated service. These integrations do not launch model calls,
cloud jobs or devices. The example workflow is outside `.github/workflows`, so a push
does not enable hosted CI. The caller supplies a result produced by the ordinary
prepare / execute / get_run flow. Workspace scopes must include `get_result_report`.

```
python -m worldfuzz.platform.ci --placement placement.json --result-id RESULT_ID --output ci-results
```

Placement files contain `version: "1.0.0"`, `kind: "remote_http"`, the local HTTP
endpoint, and `token_env` naming the environment variable containing a scoped token.
They do not contain tokens. See `docs/PLACEMENTS.md` for deployment limitations.

| Exit | JSON CI status | JUnit | Meaning |
|---|---|---|---|
| 0 | passed | pass | Requested check passed, only within the saved verification scope |
| 1 | error | error, or no report if access/transport failed | Technical error; not a robotics regression |
| 2 | violation | failure | A verified task requirement was violated |
| 3 | incomplete | skipped | Missing verification, cancelled work or exhausted budget |

A requirement violation is a **regression** only against a comparable previously
passing baseline. Use `compare_results` with its compatibility checks first. An
integrity-only pass retains `task_outcome: not_evaluated`. It does not establish a
flight or manipulation success. UI and embedded view consume the same report
operation. Old report files are removed before a new fetch, including denied fetches.

For a partner UI, import `worldfuzz/ui/result-view.mjs` into the host application's
bundle, and call `mountResult(container, {resultId, request})`, then `await refresh()`.
`request(path)` must return the WorldFuzz response envelope using the host's existing
authenticated backend proxy. Keep credentials in the proxy/session, never the URL,
markup or localStorage. The component renders text, clears stale facts on denied
refresh and offers `destroy()`. This is an embedded DOM component, not an unauthenticated
iframe. Cross-origin service access and frame policies are not relaxed.

For signed events, create a subscription using `create_webhook`, then call
`read_signed_webhook` (scope required). Configure server and local receiver with a
random `WORLDFUZZ_WEBHOOK_SIGNING_KEY` of at least 32 bytes through runtime secret
configuration. This shared secret must not be committed, logged, exported or put in
a workspace backup. A workspace token does not reveal it. Each envelope binds the
workspace, subscription, sequence, event contents and signing time using HMAC SHA-256.
Changing content, signing time or workspace invalidates acceptance. Re-fetch after
expiry; preserve the original stable delivery ID. Cursor advancement is the caller's
responsibility **after** durable receiver acceptance.

`EventInbox(path, key, workspace_id, webhook_id).receive(envelope, apply)` validates
and deduplicates. `apply(connection, payload)` must commit its local effect through
the supplied SQLite connection; callback failure rolls back both effect and inbox.
External side effects need a transactional outbox and independent idempotency. No
claim of exactly-once network delivery is made. Server delivery remains authenticated
pull; a local relay can POST the signed envelopes to its receiver. The acceptance test
does this over loopback HTTP, under two provisioned workspaces, with sequential workers.
Server-initiated remote webhooks, key rotation across multiple servers and independent
browser-host acceptance remain outside this checked scope.
