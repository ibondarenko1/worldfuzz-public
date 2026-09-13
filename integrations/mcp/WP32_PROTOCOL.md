# External MCP host setup for WP32

These are configuration examples, not installed integrations or completed agent
tests. They start the installed WorldFuzz interpreter in stdio mode, forwarding
through the usual versioned placement to an authorized workspace. Replace the two
absolute paths and configure the placement's token environment using your host's
secret mechanism. Never put bearer values in these tracked files. Do not disable
the host's normal permissions, workspace isolation or tool approvals.

Three **different client implementations**, with pinned client version and model,
are required. Three invocations of our stdio script do not meet that requirement.

| Template | Host configuration location | Primary documentation checked 2026-09-11 |
|---|---|---|
| claude-desktop.example.json | Developer settings → config; merge `mcpServers` | [MCP local-server guide](https://modelcontextprotocol.io/docs/2026-07-28/develop/connect-local-servers) |
| vscode.example.json | Profile or workspace `mcp.json`; merge `servers` | [VS Code MCP configuration](https://code.visualstudio.com/docs/agent-customization/mcp-servers) |
| gemini-cli.example.json | `settings.json`; merge `mcpServers` | [Gemini CLI MCP servers](https://geminicli.com/docs/tools/mcp-server/) |

For VS Code, pin whether the client is the extension host or Agent Host: their
configuration forwarding differs. Do not assume a template was accepted until
that host discovers the actual tools. Gemini's template retains `trust: false`.
Host approvals and manual corrections count as separate observations in WP32.

## Server names and tool names

The configured server name selects a connection. MCP `tools/list` returns bare
tool names such as `get_result_report`; `tools/call.params.name` uses that exact
bare name. With a host exposing separate fields, the arguments look like this
(an authored example, not an executed task):

```json
{
  "ServerName": "worldfuzz-task",
  "ToolName": "get_result_report",
  "Arguments": {"result_id": "replace-with-result-id"}
}
```

Do not put `worldfuzz-task/get_result_report` in `ToolName`: the host may compose
`worldfuzz-task/worldfuzz-task/get_result_report` and deny that different name.
Do not add permissions or aliases for the malformed call. Host-specific display
names and permission expressions are not tool names to send back to the server.
`describe_operation` likewise takes a bare service tool in its `name` field.
Adapter operations such as `solve_navigation` and `export_dataset` belong in
`goal.operation` of `compile_goal` or `run_goal`.

The reviewed Antigravity failure used a double prefix after successfully calling
the bare tool. The repository examples did not instruct that double prefix;
earlier server instructions omitted an explicit separate-field example. Current
instructions clarify this boundary. A host refusal can happen before WorldFuzz
receives any call; server-side error guidance cannot repair that client state.

A model-filter stop is also a client-side stop. Without a WorldFuzz semantic
decision, it is not evidence of a correct service refusal. Preserve the native
message and unknown cause; do not bypass the filter or reinterpret it as a
compatibility, rights or budget decision.

Each client receives only its `client-N-prompts.json` and operator-provided fixture
bindings from the acceptance directory. Give each task a fresh conversation and
workspace grant with a fixed budget. The agent, not a script, selects tools and
interprets the result. Retain its native trace/export and independent service
evidence before redaction/review. A transport smoke test can verify installation
but never counts toward the held-out 60. No registration, model call or host
installation was performed by these examples.
