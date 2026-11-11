# Istar-Ai-Utils

Repository for AI tooling built around OpenCode: MCP servers, skills and plugins.

```text
Istar-Ai-Utils/
├── MCPs/                     # Model Context Protocol servers
│   └── Istar-Control/        # Windows desktop + browser control MCP
├── Plugins/                  # OpenCode plugins
│   └── istar-control-panel/  # Read-only activity surface for Istar-Control
├── Skills/                   # OpenCode skills (empty placeholder)
└── opencode.json             # Project-scoped OpenCode configuration
```

## Components

| Component | Purpose | Documentation |
| --- | --- | --- |
| [`MCPs/Istar-Control`](MCPs/Istar-Control) | Local stdio MCP server: Windows window discovery, enforced consent gate with grants/stop/budget/audit, physical keyboard/mouse input, exact-HWND capture, UI Automation tools, ephemeral Playwright browser, local media inspection, and a Godot fixture harness. | [README](MCPs/Istar-Control/README.md) |
| [`Plugins/istar-control-panel`](Plugins/istar-control-panel) | OpenCode plugin that shows what the MCP server is controlling. Observer only. | [README](Plugins/istar-control-panel/README.md) |
| `Skills/` | Placeholder for OpenCode skills. | — |

## OpenCode configuration

`opencode.json` is project-scoped and reversible. It registers:

- the `istar-control` MCP server, using the project's own virtual environment;
- the `istar-control-panel` plugin, loaded from `Plugins/istar-control-panel`;
- `Skills/` as an additional skill source.

The MCP server command uses absolute paths, so they must be updated if the repository moves. Remove an entry to disable that component.

## Working conventions

- Every component keeps its own tests and documentation.
- Public surfaces are English: tool descriptions, plugin copy, and READMEs.
- Security-sensitive behaviour (physical input, capture, subprocess execution) is implemented in the component itself rather than delegated to shell commands.
- CI runs the MCP server tests (ruff + pytest on Windows) and the plugin checks (tsc + node --test on Ubuntu) on pushes to `main`/`master`; see `.github/workflows/ci.yml`.
