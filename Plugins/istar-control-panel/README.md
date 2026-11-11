# Istar-Control panel (OpenCode plugin)

Read-only companion surface for the local [Istar-Control](../MCPs/Istar-Control) MCP server.

It shows what the MCP server is currently controlling. It does **not** inject input, approve actions, grant authority or stop the server.

## How it works

1. The MCP server writes a small status file after each desktop or browser action:
   `%LOCALAPPDATA%`/`~/.local/share/opencode/istar-control/status.json`
   (override with `ISTAR_CONTROL_STATUS_PATH`).
2. This plugin's server part reads that file and exposes it as the plugin RPC `istar-control.status`.
3. The TUI part renders the status in the footer and in a session panel.

The status file contains the action, state, target handle, window title, process id and timestamp. It never contains typed text, screenshots or credentials. It is a local hint, not an authorization channel: any local process can write it, and stale entries are shown as idle.

## Enable

The server part is already enabled for this repository:

```jsonc
// opencode.json (project scope)
{
  "plugins": [{ "package": "./Plugins/istar-control-panel" }],
}
```

The TUI part is loaded by the terminal client from `cli.json`, not from `opencode.json`:

```jsonc
// ~/.config/opencode/cli.json
{
  "plugins": [{ "package": "S:/Proyectos/Istar-Ai-Utils/Plugins/istar-control-panel" }],
}
```

Remove the entry to disable it.

## Call the RPC directly

The RPC is registered per location, so HTTP callers must pass the location:

```powershell
opencode api post '/api/rpc/istar-control/status?location[directory]=S:\Proyectos\Istar-Ai-Utils' --data '{"input":{}}'
```

## Develop

```powershell
bun install
bun run typecheck
bun run test
```

The package pins `@opencode/plugin` to the version of the installed OpenCode release (2.0.27) because the plugin API changes between releases.
