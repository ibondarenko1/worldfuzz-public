# Contributing to WorldFuzz

Start from the current shared platform service and its documented acceptance
scope. The repository is under review; public contribution channels and a public
package release are not implied by these instructions.

## Propose an adapter

Read docs/ADAPTERS.md (ADAPTERS.md in the separately supplied developer kit).
Use `examples/adapters/trajectory_extent` or unpack its supplied sdist. An adapter
is trusted installed Python code, enabled through the existing pinned registry.
Keep new domain code in that distribution; do not create a parallel runner or
bypass scoped service calls, source rights, budgets, snapshots or verification.

Provide a focused change with:

- The intended operation and profile, supported input formats, time, units,
  frames, actions and explicit unknowns; mark authored fixtures accordingly.
- Exact Python/core/adapter/dependency versions and the entry-point pin, plus
  every operation's source_contract, rights and resource requirements.
- A small redistributable input with license/provenance, expected result and
  honest evidence scope. Never include private workspaces, credentials or HDF5
  merely to make a demonstration convenient.
- Targeted positive and negative checks: wrong input kind, missing rights,
  changed input, unsupported required capability, and the claimed result level.
- Installation and one common-service execution outside checkout, with separate
  worker evidence and core hashes before/after adapter installation.

Do not treat missing optional adapters as passed integration. Unsupported replay
must refuse rather than substitute recorded states. Published benchmark cases
and authored extension fixtures remain separate. Data-source permissions are
not granted by this repository's code license.

## Submit a reviewable change

For an account that already has repository access, use its existing GitHub PR
workflow against the maintainer-designated branch. Otherwise prepare a patch
and minimal reproducer for the repository owner to review through an available,
authorized channel. Do not assume public Issues, Discussions, a support email or
a chat room has been provisioned. Never overwrite branch history to submit work.

Describe the concrete before/after behavior, changed contracts, targeted results,
known limitations and installation impact. Keep acceptance receipts immutable;
append follow-up evidence instead of rewriting historical scores. New package
bytes require a new version/hash. A maintainer reviews compatibility, licenses,
trust/resource boundaries and evidence before selecting a release candidate.

For reproducible bugs, redaction and maintenance policy, see docs/SUPPORT.md and
docs/COMPATIBILITY.md (same basenames in the kit). No response-time or fix-date
commitment is made. Internal checks are not external-user or adoption metrics.
