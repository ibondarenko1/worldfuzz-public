# Build and serve the WP34 documentation

The platform's existing `/docs` route now redirects to `/docs/`. English HTML
pages need no JavaScript. Packaged prose lives in `worldfuzz/documentation/`;
`worldfuzz.platform.documentation` renders it with the same service contracts
and bundled adapter descriptors. `/openapi.json` retains its existing API path.
Do not edit a generated second copy of a schema.

```sh
python -m worldfuzz platform serve
```

The default canonical base is the actual loopback listener. Configure an
operator-chosen future publication domain with `serve --docs-base-url URL` or
`WORLDFUZZ_DOCS_BASE_URL`. Configuration changes documentation links only, not
the server's loopback binding, permissions or publication state.

To prepare a static site in a new directory, supply its intended URL explicitly:

```sh
python -m worldfuzz platform docs --out prepared-docs --base-url http://127.0.0.1:8766
```

Use an operator-owned HTTPS base URL for a future non-loopback placement. A base
path is supported. `docs/*.html`, `docs/docs.css`, generated `docs/openapi.json`,
`sitemap.xml` and a linked `llms.txt` are written without a workspace, credentials,
model calls or publishing. Existing output directories are rejected. This does
not establish a live website, indexing, PyPI availability or an MCP registry entry.

The command runs from an installed wheel as well as a checkout: all prose, style,
workflow examples and generators are package data. The rc1/rc2 release archives
remain immutable. rc3 contains the documentation changes; WP33's accepted matrix
is not relabeled as rc3 installation/container evidence.

In a repository checkout, the Markdown dependency audit is generated from the authoritative JSON using
`python evaluation/sync_dependency_audit.py`. Use `--check` for drift detection.
Historical receipts and negative WP31/WP32 results stay unchanged. User instructions
are prose; operation schemas, examples and profile contracts come from the existing
code. Each human workflow example is validated against those contracts in the
focused WP34 checks.
