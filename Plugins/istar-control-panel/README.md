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

The plugin is already enabled for this repository through `opencode.json`:

```jsonc
// opencode.json (project scope)
{
  "plugins": [{ "package": "./Plugins/istar-control-panel" }],
}
```

The server part loads automatically. The TUI part loads automatically on TUI start; do **not** also add it to `cli.json` (the same package declared in both places can collide).

### Verify that the client loaded it

```powershell
opencode api get "/api/plugin?location[directory]=S:/Proyectos/Istar-Ai-Utils"
```

Look for an entry with `id: "opencode-istar-control-panel"` and `features: { "server": true, "tui": true, "rpc": true }`, `state: { "status": "active" }`.

### Use it in the TUI

- Open the command palette (`Ctrl+P`) and run **Toggle Istar-Control activity** (also bound to `Ctrl+Alt+I`).
- The session panel shows the latest known status: the action, a stale marker if the last status is old, and a reminder that this panel is observer-only and cannot approve, block or stop desktop input.
- The home footer shows the same one-line summary and refreshes every ~2 seconds after an actual action.

### Exercise a status read directly

```powershell
opencode api post "/api/rpc/istar-control/status?location[directory]=S:/Proyectos/Istar-Ai-Utils" --data '{"input":{}}'
```

Returns the published status file (e.g. `state: idle | completed` with `age_seconds` and `stale` flags). Typed text, screenshots and credentials are never in the file.

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
