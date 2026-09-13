# Reproducible reports and maintenance

Use the repository review channel available to your authorized account. If none
is available, prepare a report for the owner; this document does not assert a
public issue tracker, monitored email, chat room, staffed support team or SLA.
For a sensitive report, first obtain a private route from the owner. Do not put
credentials, private datasets or another organization's identifiers in a public PR.

## A useful report

Include the expected behavior, observed error code and next_action, smallest
request, timestamp/time zone, reproducible steps and whether the failure occurred
at installation, preparation, execution or result reading. Retain the first
attempt and original idempotency key locally. An anonymized report may replace
it consistently; do not retry live work with a new key to disguise a failure.

Record Python and OS versions, core and adapter versions, entry-point pin,
package SHA-256, placement kind, runner enforcement mechanism, source/profile
versions and verification mode. Include relevant get_run/get_result_report and
get_run_audit output, and a small rights-cleared fixture where possible.
Do not attach a whole database or a confidential source to obtain support.

Before sharing, review and redact tokens, passwords, API keys, environment
values, private URLs, local usernames/paths, principal/workspace IDs and source
content. Consistently replace IDs when relationships matter. Preserve original
hashes privately; redacted files need their own hashes and a redaction note.
`get_service_metrics` counters do not include request bodies, but audit output
can identify principals and workspaces. Redaction is still required.

## Triage and follow-up

Maintainer review distinguishes request/compatibility/rights refusals, execution
errors, resource exhaustion, cancellation, unfinished recovery and a measured
robotics regression. Process exit zero or a plan response alone proves none of
these. Record confirmed cause, affected versions, fix/workaround, targeted test
and remaining uncertainty. Avoid promising dates or supported deployments that
have not been tested.

Keep the reproducer small and sequential. Recover from uncertain attempts
before retrying, preserve partial artifacts and perform upgrades on a separate
copy. Announce intended removals and replacement/version paths in repository
release notes before withdrawing a supported adapter. See COMPATIBILITY.md for
the current evidence and rollback boundary, and USAGE_METRICS.md for local
operational exports. No automatic report or analytics upload is implemented.
