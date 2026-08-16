# Connect HuMetric to your AI assistant

The HuMetric MCP server lets Claude — or any MCP-compatible assistant — work with
your HuMetric data directly: submit signals, read entity metrics, ask why a metric
moved, manage Metric Packs, and approve pending reviews. No copy-pasting between
a dashboard and a chat window.

Setup is two steps: **install the server**, then **point your client at it**.
Budget about five minutes.

> 🇹🇷 Bu sayfanın Türkçesi: [MCP Kurulumu](/mcp-kurulum)

## What you'll need

| | |
|---|---|
| **A HuMetric API key** | Starts with `hm_live_`. Create one in the dashboard under **Settings → API Keys**, or via `POST /v1/api-keys` (see [Authentication](/guide/authentication)). |
| **Your API base URL** | `https://api.gethumetric.com` for the hosted service, or `http://localhost:8002` if you run HuMetric yourself. |
| **uv** | The installer. If you don't have it: `curl -LsSf https://astral.sh/uv/install.sh \| sh` (macOS/Linux) or `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` (Windows). |

Keep the API key somewhere safe for the next step — it is shown only once when created.

## Step 1 — Install the server

You do not need to clone the repository or set up Python. One command:

```bash
uvx --from git+https://github.com/bestekarx/humetric.git humetric-mcp --help
```

This downloads the server, installs it in an isolated environment, and prints its
usage. It takes a few seconds. If you see the usage text, you're ready.

::: tip Why `uvx`?
`uvx` runs the server without touching your system Python or leaving a virtualenv
for you to manage. The MCP server is a thin HTTP client — it installs about 30
small packages and no database drivers.
:::

## Step 2 — Connect your client

Pick your assistant below.

### Claude Code

One command — run it anywhere:

```bash
claude mcp add humetric --scope user \
  --env HUMETRIC_MCP_API_KEY=hm_live_your_key_here \
  --env HUMETRIC_BASE_URL=https://api.gethumetric.com \
  -- uvx --from git+https://github.com/bestekarx/humetric.git humetric-mcp
```

`--scope user` makes HuMetric available in every project and keeps your API key
out of any repository. Use `--scope local` instead if you want it only in the
current project.

Verify with `claude mcp list` — you should see `humetric: ... ✔ Connected`.

### Claude Desktop

Edit your config file:

- **macOS** — `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows** — `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "humetric": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/bestekarx/humetric.git",
        "humetric-mcp"
      ],
      "env": {
        "HUMETRIC_MCP_API_KEY": "hm_live_your_key_here",
        "HUMETRIC_BASE_URL": "https://api.gethumetric.com"
      }
    }
  }
}
```

Restart Claude Desktop completely (quit, don't just close the window). HuMetric
then appears in the tools menu.

::: warning If Claude Desktop can't find `uvx`
Claude Desktop launches servers without your shell's `PATH`, so a bare `uvx` may
not resolve. Run `which uvx` (macOS/Linux) or `where.exe uvx` (Windows) and put
the **full absolute path** in `"command"` — for example
`/Users/you/.local/bin/uvx`. This is the single most common setup failure.
:::

### Cursor

Create `.cursor/mcp.json` in your project (or `~/.cursor/mcp.json` for all
projects) with the same `mcpServers` block shown for Claude Desktop above.

### VS Code (GitHub Copilot)

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "humetric": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/bestekarx/humetric.git",
        "humetric-mcp"
      ],
      "env": {
        "HUMETRIC_MCP_API_KEY": "hm_live_your_key_here",
        "HUMETRIC_BASE_URL": "https://api.gethumetric.com"
      }
    }
  }
}
```

Then open Copilot Chat in **Agent** mode and enable the HuMetric tools.

::: danger Don't commit your key
`.cursor/mcp.json` and `.vscode/mcp.json` live inside your repository. Add them
to `.gitignore`, or use a user-level config instead.
:::

## Step 3 — Check that it works

Ask your assistant:

> Use the humetric_health tool and tell me the status.

A healthy reply reports the API status and whether the worker is running. If you
instead get a message about a missing or rejected API key, jump to
[Troubleshooting](#troubleshooting).

Then try something real:

> List my entities, then explain the top-scoring metric on the first one.

## What your assistant can now do

25 tools, grouped by area:

| Area | What it covers |
|---|---|
| **Signals** | Submit raw text signals, check processing status, read the full extraction trace |
| **Entities & metrics** | Create/update entities, read current metrics, explain a metric, read its history |
| **Query** | Natural-language search across entities with hybrid retrieval |
| **Metric Packs** | List, read, create, and update Pack definitions |
| **Human review** | List metrics awaiting approval and approve or reject them |
| **Consent (KVKK/GDPR)** | Read, grant, and revoke consent scopes |
| **Account** | Dashboard summary, usage report, call history, audit log, health check |

Three guided workflows ship as MCP prompts: `analyze_entity`,
`investigate_signal`, and `draft_metric_pack`.

Deliberately excluded: account registration, API-key creation, BYOK provider
secrets, and billing. An assistant should not be able to mint its own
credentials or move money. See [MCP reference](/mcp) for the full rationale.

::: tip Signals are processed asynchronously
Submitting a signal returns `status: "received"`, not finished metrics. The
server tells your assistant to poll with `humetric_get_signal` until the status
is `completed` before reading metrics — so don't be surprised by a short wait.
:::

::: info Your calls are logged as metadata
Every request the MCP server makes is recorded against the API key that made
it: which tool, which endpoint, the HTTP status and how long it took. Signal
text, entity content and tool arguments are **not** recorded. Ask your
assistant for `humetric_call_history` to see your own activity — for example
"which tools did I use most this month?".
:::

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `HUMETRIC_MCP_API_KEY` | — | **Required.** Your `hm_live_...` API key |
| `HUMETRIC_BASE_URL` | `http://localhost:8002` | HuMetric API address |
| `HUMETRIC_MCP_TIMEOUT_S` | `30` | Per-request timeout, in seconds |
| `HUMETRIC_MCP_MAX_ITEMS` | `50` | Max list items per response before truncation |
| `HUMETRIC_MCP_LOG_LEVEL` | `INFO` | Log level; logs go to stderr |

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Server failed to start" / "server disconnected" | Almost always `uvx` not being found. Use the absolute path from `which uvx` in `"command"`. |
| Every tool reports a missing API key | `HUMETRIC_MCP_API_KEY` isn't reaching the server. Check it's inside the `env` block and that you fully restarted the client. |
| `401 invalid_api_key` | The key is wrong or was revoked. Create a fresh one in the dashboard. |
| Tools time out | `HUMETRIC_BASE_URL` is unreachable. Confirm the URL, and if self-hosting, that the API is running. |
| `502 llm_auth_failed` | Server-side problem, not yours: the HuMetric deployment's LLM provider key is invalid. Contact your administrator. |
| Signals stay `queued` forever | The background worker isn't running. Ask for `humetric_health` to confirm, then restart the worker. |
| `ModuleNotFoundError: mcp.server.fastmcp` | You're on an old checkout that allowed `mcp` 2.x. Pull the latest and reinstall — the dependency is now pinned to the 1.x series. |

## Self-hosting and remote access

Running HuMetric yourself? Point `HUMETRIC_BASE_URL` at your own deployment —
everything else on this page is unchanged.

To serve one MCP endpoint to several clients over the network instead of running
it locally per user:

```bash
humetric-mcp --transport sse --port 8765
humetric-mcp --transport streamable-http --port 8765
```

::: warning
The API key is read from the server process's environment, so a networked
instance acts on behalf of that single key for every client that connects. Run
one per tenant, and never expose it to an untrusted network.
:::

For the complete tool list, scope decisions, and design notes, see the
[MCP reference](/mcp).
