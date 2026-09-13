# M6 acceptance, September 11, 2026

WP25?WP28 meet their recorded local acceptance scope. Organization/workspace
isolation, common-runner execution, separate authorized recovery, reconciliation
of unknown operations and server quota enforcement were exercised. M7 has not
been entered, and this is not completion of the full product plan.

| User capability | Acceptance evidence |
| --- | --- |
| Scoped organizational access | Two local workspaces isolate runs, artifacts, datasets, campaigns, suites, search and pull webhook events. Cross-organization login/job access is also denied with equal principal names. |
| Local and remote-protocol deployment | Published navigation executes in source-local and separately installed-wheel loopback HTTP instances; exact task records/source versions match and independent verification passes. |
| Operational recovery | A separate restored workspace returns metadata, registered final/partial blobs, history and result access. Owner restrictions/revocation persist. Interrupted artifact retention resumes once on disposable data. |
| Workspace quotas | One logical job/reservation survives four-interface repeated requests; new requests exceeding quota are atomically denied. Estimates, observed phase samples and unknown/conservative holds are separate. |

The single sequential full regression at a802c43 ran 291 tests: 264 passed, 27
were skipped, none failed (136.625 seconds including discovery). Skips are 13
POSIX-only storage/ownership checks, nine opt-in isolated PostgreSQL checks,
three optional legacy-navigation dependency checks and two Can-runtime checks
unavailable in the base interpreter. Exact reasons are in checks.json. Existing
PostgreSQL schema/repository behavior was unchanged, so no cluster was started.
The 209-source corpus hash still equals its retained baseline. No source restore
or HDF5 download occurred.

Final review then corrected recovery grants to preserve owner policy instead of
resetting it. Four affected recovery tests, one organization-namespace test and
one updated installed-placement test passed. These overlap earlier coverage and
are not added to the regression count. No second full regression was run. See
[policy review](../m6-policy-review-20260911/README.md).

OpenAPI and request examples were regenerated from the common contracts and
checked for exact agreement. The final wheel was reviewed for package-only files
and exact Python source bytes; no database, HDF5, credentials or connection package
was included. Runtime dependencies and final wheel identity are recorded in the
placement and policy-review receipts.

The final process inventory found no project worker, probe or test executor.
Free RAM was 8.24 GiB and commit 53%. The executor's kill-on-close Windows Job
Object bounded only its own descendants. No unrelated process was stopped.
No Docker, PostgreSQL, paid API, cloud job, training, device or publication ran.

Limitations: Compose is prepared but unexecuted on Kali/Linux; the two accepted
placements share one Windows host. External SSO, public/cross-host operation and
PostgreSQL tenant job storage are unverified. Workspace backup excludes authority
credentials; authority disaster recovery remains an OS-protected operator task.
Retention covers registered task artifacts, not source datasets or audit history.
These limitations must not be described as accepted deployments or capabilities.
