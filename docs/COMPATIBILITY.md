# Compatibility, upgrades and end of support

Current developer candidate: core **0.4.0rc5**, example adapter **0.2.0**.
Candidates are locally supplied immutable wheel/sdist files with recorded
SHA-256. Neither a published release nor broad client/platform certification is
implied. Patch-level numbers alone do not establish cross-version compatibility.

| Layer | Current contract and rule |
| --- | --- |
| Core Python | Declares Python >=3.11. WP33 tested 3.11.15 and 3.14.3 on its pinned rc1 packages; a later candidate does not inherit a newly executed matrix |
| Installed adapter | Four-method contract 1.0.0; unique identities, closed request schemas and mandatory source_contract. Enable an exact entry-point/distribution pin |
| Example dependency | 0.2.0 declares worldfuzz>=0.4.0rc4,<0.5. The WP36 outside-checkout execution tests rc5 only; other versions in the range remain untested combinations |
| Service/entity schemas | Version 1.0.0; runtime output validation and generated references are authoritative. Preserve fields and unknown values; changes to meaning require a versioned contract |
| Exact JSON | nanoseconds-decimal.v1 projects exact ns values as decimal strings; original stored bytes and hashes are not rewritten |
| Runner | Protocol 1.0.0 pins actual environment, adapter files, source versions and enforced limits. An unsupported mandatory mechanism blocks work |
| Verifier | Requested mode, performed checks and proven_result are separate; changing a protocol needs explicit support and targeted evidence |

An added optional capability must not weaken prior permission or schema checks.
Incompatible input/output meaning requires a new profile/operation or explicit
version boundary, a migration note and a targeted consumer check. Do not
silently reinterpret existing rows. Unknown frame, controller or license facts
remain unknown. No automatic arbitrary adapter API migration is implemented.

## Updating core or adapter

Pin both distributions and record their hashes. Stop accepting new work, let
active work finish or cancel it, and reconcile uncertain attempts before retrying.
Use the supported consistent backup and restore into a new workspace; verify
restored metadata, blobs, rights, revocations and saved results before switching
clients. Restart the service/worker environment after changing package files.
Registry and environment changes invalidate unfinished old plans; prepare a new
plan under the new version rather than mutating historical evidence.

The supported rollback is a restore of the pre-upgrade backup into another new
destination using the matching old runtime. In-place downgrade and opening a
newer catalog with an older runtime are not promised. Keep the old environment,
archives and backup until acceptance. The detailed procedure and its historical
matrix are in RELEASE_INSTALLATION.md and the repository's WP33 receipts.

| Historical receipt | Actual evidence retained |
| --- | --- |
| WP33 rc1 | Native Python 3.11.15/3.14.3 install; UI/MCP; separate 0.3.0 to rc1 workspace upgrade preserving 209 episodes and associated restrictions |
| WP33 rc2 | Isolated Kali/Linux container, scoped access, navigation and persistence after recreation; shared runtime comparison with rc1 |
| WP34 rc3 | Installed-wheel plain HTML/browser/quickstart and package content |
| WP35 rc4 | Prepared Registry metadata, local skill installation and one successful subscribed-client integration after retained setup diagnostics |
| WP36 rc5 | Scope to check: external example kit installation and common-service numeric execution; core unchanged by adapter installation. The separate WP36 receipt determines acceptance |

Each row describes that receipt's scope, not a retest on rc5. No rc5 container,
cross-platform matrix, corpus migration or independent agent study is claimed.
The previous archives remain immutable. Current instructions are distinct from
the instructions frozen inside older archives.

## Deprecation and support boundaries

For a planned incompatible change, document the affected core/adapter/schema
versions, replacement, data implications, recovery procedure and intended last
supported version before release. Maintain explicit pins and readable historical
evidence. Do not promise an untested compatibility window or automatic migration.
If continued safe support is unavailable, document the limitation and recommend
the last tested configuration or disabling the adapter. There is no published
calendar-based support lifetime or SLA. Source permissions and confidentiality
continue to apply after software support ends.
