# Local organizations and workspace isolation (WP25)

The organization gateway authenticates provisioned principals, issues expiring
opaque tokens scoped to one workspace and an explicit operation list, and checks
current membership and token revocation on every request. Passwords use salted
scrypt; only password hashes and token SHA-256 fingerprints are persisted.
Keep the authority database outside the checkout and protect it with OS permissions.
Never log login responses or place passwords/tokens in command-line arguments.

Provision through the trusted Python operator API, using `getpass.getpass()` for
personal password input:

```python
from getpass import getpass
from worldfuzz.platform.identity import IdentityStore
authority = IdentityStore("/private/worldfuzz/identity.sqlite3")
authority.provision("my-org", "owner", getpass(), "workspace-a",
                    "/private/worldfuzz/a/results", "/private/worldfuzz/a/catalog.sqlite3",
                    ["get_capabilities", "find_resources", "run_goal", "compile_goal",
                     "prepare_plan", "execute_plan", "get_run", "get_result",
                     "get_result_report", "get_artifact_json", "get_budget",
                     "cancel_run", "reconcile_run", "resume_run"])
```

Run `worldfuzz platform --authority /private/worldfuzz/identity.sqlite3 serve`.
`organization_login` is a common, schema-validated gateway operation exposed to
HTTP/SDK and CLI; supply the password from protected local input. The resulting
token is used by the SDK or in `WORLDFUZZ_SESSION_TOKEN` for CLI/MCP with
`--authority`. The token is never a URL parameter. The organization gateway does
not expose a `/bootstrap` token. MCP starts with an existing authenticated token.
Read-only scope errors are identical across HTTP, SDK, CLI and actual MCP stdio.

Every workspace has disjoint artifact and catalog paths; names and request IDs
cannot choose another workspace. Existing catalog rows are retained on migration.
An organization-bound catalog cannot be opened as unauthenticated `local-owner`.
Worker launch carries a token fingerprint and authority path, rechecks current
membership/token scope, and rejects a different catalog/root. Revocation before
worker start prevents an attempt from being acquired. Recovery requires the
original still-valid execution session; after its revocation prepare a fresh plan
using a newly authorized session, after reconciling the old job.

Membership delegates operation access. Existing versioned source rights,
CompatibilityDecision and owner PolicyGrant checks still apply independently.
Organization membership cannot make unknown source rights verified. New source
versions still go through the existing M1/M2 checks.

Webhook support is an authenticated **pull outbox** over durable workspace events:
`create_webhook`, then `read_webhook` with a cursor. It does not send to an external
URL. It checks current result access for every event. Artifacts, datasets,
campaigns, suites and verified-result search use the same bound service.

This is local organization login under a trusted filesystem operator. External
OIDC/SAML SSO, public Internet serving, network sandboxing and push webhook delivery
are not implemented or claimed. PostgreSQL service/job storage remains outside
this SQLite organization boundary; organization workers reject an external catalog
until its per-workspace resolver is configured. Existing PostgreSQL paths are unchanged.
