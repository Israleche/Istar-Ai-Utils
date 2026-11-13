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

The TUI part is loaded automatically by the terminal client from the plugins declared in `opencode.json` (a `./tui` entrypoint is picked up automatically for this plugin). Do **not** also add it to `cli.json`: the same package declared in both places can collide, and the server-connected client loads the server plugin list instead.

Once the TUI starts, it will show the activity footer and expose the `istar-control.activity.toggle` (`Ctrl+Alt+I`) and `istar-control.activity.status` commands. Until the first real TUI launch, the live render of this part remains unverified in this project.

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
