# Connect your agent to WorldFuzz

WorldFuzz exposes the same authorized platform operations over local MCP stdio.
Use one client you already have; three clients are not required for normal use.
The old three-client research procedure is preserved in [WP32_PROTOCOL.md](WP32_PROTOCOL.md).

## Install the supplied Python package

Use Python 3.11+ and the operator-supplied, SHA-256 checked wheel in an isolated
environment. The current integration candidate is `0.4.0rc6`:

```sh
python -m pip install /path/to/worldfuzz-0.4.0rc6-py3-none-any.whl
```

This local file example does not claim PyPI publication. There is no npm wrapper.
For navigation, the base package includes the published maps and required runtime.
Other profiles may require optional packages and authorized existing source data.

## Bind a workspace

Save an absolute-path placement file outside the checkout. For a new personal
workspace owned by the local filesystem user:

```json
{"version":"1.0.0","kind":"local","root":"/absolute/private/workspace/results","store":"/absolute/private/workspace/catalog.sqlite3"}
```

For an existing organization workspace, use the operator's authority and token:

```json
{"version":"1.0.0","kind":"local","authority":"/absolute/private/authority.sqlite3","token_env":"WORLDFUZZ_SESSION_TOKEN"}
```

Inject the existing scoped token using the client's secret mechanism. Do not put
it in the placement, skill, server.json or repository. Organization membership
and source rights remain distinct. A personal placement is not an alternative
way to open another organization's bound catalog.

## Connect your existing client

Merge only the WorldFuzz entry into the configuration of the client you choose:

```json
{
  "mcpServers": {
    "worldfuzz": {
      "command": "/absolute/path/to/installed/python",
      "args": ["-m", "worldfuzz", "platform", "--placement", "/absolute/private/placement.json", "mcp"]
    }
  }
}
```

Use Windows absolute paths with doubled backslashes in JSON. The installed
`worldfuzz` console entrypoint takes the same arguments starting at `platform`.
The prepared Registry metadata uses that console entrypoint via `uvx`. Registry
installation is a future path, pending package publication and ownership checks.
Do not run an unverified same-named package from a public index.

Start the client, approve this specific local server as appropriate, and check
that it discovers `find_resources` and `run_goal`. The server label `worldfuzz`
is not part of the bare tool name. Let the host add its prefix once; do not put
`worldfuzz/run_goal` into a separate `ToolName` field. `solve_navigation` is an
adapter operation inside `goal.operation`, not another MCP tool.

Templates for [Claude Desktop](claude-desktop.example.json),
[VS Code](vscode.example.json) and [Gemini CLI](gemini-cli.example.json) retain
their client-specific structures. A template is not evidence that a host was run.

## Install the optional Agent Skill

The supplied `worldfuzz-agent-0.4.0rc6.zip` contains the small `worldfuzz/` skill
folder and `mcp/` connection files. Check its SHA-256 from the build receipt.
Extract into a new staging folder. In a separate Claude Code project, copy only
the `worldfuzz/` folder into `.claude/skills/`, preserving `SKILL.md` and
`references/navigation.md`. Keep any existing same-named skill; inspect it before
choosing a replacement. Other Agent Skills clients use their documented project
skill location. Installing files is separate from confirming that the client
loaded and used them.

For an isolated Claude Code session, use a project-local MCP configuration and
`--strict-mcp-config --mcp-config /absolute/project/mcp.json`; this leaves global
MCP settings intact. Ask the client to use `/worldfuzz`:

> Use the worldfuzz skill to run published resource movingai:0 with A*. Allow
> one task, at most 30 seconds wall time, 20 CPU seconds, 256 MiB RAM and 1 MiB
> artifacts. Wait at most 20 seconds per call. Report the actual run ID, resource,
> outcome, performed verification and artifact hashes. Do not claim physical replay.

An organization token needs discovery/budget reads plus `run_goal`, `compile_goal`,
`prepare_plan`, `execute_plan`, `get_run`, `get_result`, `get_result_report` and
`get_artifact_json` if artifacts are requested. The server checks internal scopes.
After a wait timeout, retain the run ID, exact goal and idempotency key. A prepared
plan is not an executed task. Export requires completion and existing files.

## Documentation and delivery status

WP34 pages are served locally at `/docs/`: quickstart, clients, tasks, comparison,
export and errors. `worldfuzz platform docs --out NEW_DIRECTORY --base-url URL`
exports ordinary HTML without publication. No hosted site is assumed.

The repository [server.json](server.json), also included beside these instructions in the kit, is checked against the vendored
[official 2025-12-11 schema](schema/server-2025-12-11.schema.json). Its proposed
name is `io.github.ibondarenko1/worldfuzz`; the README's `mcp-name` marker is
included in Python package metadata. Neither marker nor a successful schema
check proves namespace ownership or registry presence. Check collisions again
immediately before any separately authorized publication.

Primary requirements: [MCP Python packages](https://modelcontextprotocol.io/registry/package-types),
[Agent Skills format](https://agentskills.io/specification),
[Claude project skills](https://code.claude.com/docs/en/skills).
WP33 remains accepted only in its tested scope; M7 remains limited and its
historical benefit and independent-agent limitations still apply.
