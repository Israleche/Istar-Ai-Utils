# Istar-Control

Local stdio MCP server for explicitly targeted Windows desktop input and window capture, an ephemeral Playwright browser, and local media inspection.

## Install

Prerequisites: Windows, Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/). From `S:\Proyectos\Istar-Ai-Utils\MCPs\Istar-Control` in PowerShell:

```powershell
uv sync --extra test --extra wgc
uv run playwright install chromium
```

`inspect_media` additionally requires `ffprobe` on `PATH`. On this Windows ARM64 machine, the project virtual environment used an x64 Python executable under emulation (PE machine `0x8664`); a focused Godot window, physical arrow input and screen-crop capture were tested in that setup. A native ARM64 Python (`0xaa64`) independently enumerated the game window and mapped the right-arrow scan code; the complete MCP, screenshots, and input were not tested under native ARM64 Python. Mixed-DPI configurations and browser architecture remain unverified.

## OpenCode configuration

The project root is `S:\Proyectos\Istar-Ai-Utils`. Its `opencode.json` registers `istar-control` under the OpenCode V2 `mcp.servers` configuration, using the Python executable in this project's `.venv` and `MCPs/Istar-Control` as its working directory. The paths are absolute and must be changed if the project moves. This is project configuration, not a global installation. OpenCode discovers it when working in the project scope; verify the connection in your OpenCode session. See [V2 MCP configuration](https://opencode.ai/v2/docs/mcp-servers/) and [V1 migration](https://opencode.ai/v2/docs/migrate-v1).

The MCP server is not an OpenCode Desktop GUI plugin. The separate [GUI plugin API](https://opencode.ai/v2/docs/build/plugins/gui) is experimental. The project ships an optional companion plugin at `Plugins/istar-control-panel` that publishes the server's activity status to OpenCode; it is an observer only. Desktop input attempts display a best-effort, non-activating indicator with an action and HWND. It can be obscured and is not an authorization control; `COMPLETED` means the call ended, not that the application responded.

## Activity status and the OpenCode companion

Every desktop or browser action publishes a small status file, by default `%LOCALAPPDATA%`/`~/.local/share/opencode/istar-control/status.json` (override with `ISTAR_CONTROL_STATUS_PATH`). It contains only the action, state, target handle, window title, process id and timestamp. Typed text, screenshots and credentials are never written to it, and the allowed operation names are fixed.

The companion plugin reads that file and exposes it read-only over plugin RPC as `istar-control.status`. It cannot inject input, approve an action or stop the server. The RPC is registered per location, so an HTTP caller must pass the location:

```powershell
opencode api post '/api/rpc/istar-control/status?location[directory]=S:\Proyectos\Istar-Ai-Utils' --data '{"input":{}}'
```

The status file is a local hint, not an authorization channel: any local process can write it, and a stale file is reported as idle.

## Tools

These are MCP tool names, not shell commands. The client supplies arguments using the tool schema.

| Tool | Purpose |
| --- | --- |
| `list_windows` | List visible titled Windows windows and handles. |
| `focus_window` | Request foreground focus for a handle. |
| `desktop_screenshot` | Return a PNG screen crop of a foreground window. |
| `desktop_capture_window` | Capture the specified HWND using WGC without a screen-crop fallback; requires the `wgc` extra. |
| `desktop_press_key` | Send one physical key, held for 0–2 seconds. |
| `desktop_press_keys` | Hold 1–4 distinct physical keys for 0–2 seconds. |
| `desktop_show_indicator` | Display a temporary, best-effort generic control indicator. |
| `desktop_type_text` | Send Unicode text, up to 1024 UTF-16 code units. |
| `desktop_click` | Click at client-relative physical pixel coordinates. |
| `desktop_move`, `desktop_scroll`, `desktop_drag` | Move, scroll, or perform a bounded drag in a target's client area. |
| `browser_open` | Launch a fresh Chromium session at an HTTP(S) URL; headless by default. |
| `browser_navigate` | Navigate the active session to an HTTP(S) URL. |
| `browser_back`, `browser_press`, `browser_scroll`, `browser_scroll_into_view` | Navigate and interact within the owned browser. |
| `browser_aria_snapshot` | Read a bounded ARIA snapshot; it may contain sensitive page values. |
| `browser_snapshot` | Return normalized page body text; default 4,000, maximum 20,000 characters. |
| `browser_screenshot` | Return a PNG viewport screenshot. |
| `browser_click` | Click a Playwright selector. |
| `browser_fill` | Fill a Playwright selector. |
| `browser_close` | Close the browser and discard its context. |
| `inspect_media` | Return local audio/video duration and stream metadata via ffprobe. |
| `media_frame`, `media_audio_statistics` | Extract an in-memory frame or measure bounded peak, RMS and quiet-sample fraction with ffmpeg. |
| `control_request` | Ask a human to approve desktop control of one window via a local dialog; issues a short-lived grant with capabilities (`focus`, `capture`, `input`).
| `control_revoke`, `control_stop`, `control_resume` | Revoke one/all grants; emergency stop that blocks every desktop action; clear the stop.
| `control_status` | Published activity state, grants, stop flag, budget and audit path.
| `uia_observe` | Bounded UI Automation element metadata (limit 1–500); password controls omitted.
| `uia_invoke`, `uia_set_value`, `uia_toggle` | Window-scoped UI Automation actions on exactly one matching non-password control; value support is write-only.
| `media_compare_frames`, `media_audio_profile` | Coarse frame-difference signal and descriptive 8 kHz PCM stats for local media. |

The server also exposes the resource `istar-control://guide` and the prompt `computer_use_workflow`.

## Usage and safety

Every desktop action requires an active **grant** for the target window. Consent is a local Windows dialog raised by `control_request`; only an explicit Yes creates a short-lived grant (default 5 minutes, scope: that HWND + the process it had at grant time, with `focus`, `capture`, `input` capabilities). `control_status` lists active grants, the stop flag and the budget used. `control_stop` revokes every grant and blocks all desktop actions; `control_resume` clears the stop flag but does not restore old grants.

Grants expire on TTL, on process change, on window invalidation and on an explicit stop; the session budget (default 300 actions) and per-minute budget (default 60) also apply. Audit events are written to `$ISTAR_CONTROL_DATA_DIR/audit.jsonl` (default `%LOCALAPPDATA%\opencode\istar-control\audit.jsonl`) with rotation; typed text, screenshots and credentials are never audited. Set `ISTAR_CONTROL_DATA_DIR` to relocate it.

Automation and tests may set `ISTAR_CONTROL_AUTO_APPROVE=1` to skip the dialog entirely; the stop flag, budgets and audit still apply and the bypass applies to first use of a window. Use it only on machines you control.

1. Ask the assistant to list windows, then identify the intended `hwnd` from the result.
2. Call `control_request` for that handle; a local dialog shows the window title, executable and requested capabilities. Denial, timeout or error is treated as denied.
3. Inspect `desktop_capture_window` for an exact-HWND WGC capture, or focus the visible, non-minimized window and use `desktop_screenshot`. The latter is a **window-rectangle screen crop**, not an isolated window capture: overlapping windows and sensitive material may appear. Protected, accelerated, or off-screen content may be blank or incomplete. WGC never silently falls back to a crop; HWND reuse remains possible.
4. Perform one bounded action and inspect the result before continuing. Screenshot pixels include window borders; click coordinates start at the client-area origin, so they are not interchangeable. The implementation requests per-monitor DPI awareness and fails if it cannot; mixed-DPI layouts and changes in window position still require care.
5. For web tasks, open a browser, inspect a snapshot or screenshot, act using selectors, verify the result, then close it. For consequential actions such as purchases, messages, deletion, or security changes, obtain human confirmation first.

Desktop events are **global physical Windows input**, not messages confined to the selected handle. Focus can change after validation and before injection, redirecting an action. Windows UIPI, the target application, raw-input handling, or focus restrictions may reject or ignore events. A successful tool response does not establish that the intended application received or acted on input. The key hold is bounded and releases are attempted even on focus loss; Windows can still reject a release. Some system shortcuts are blocked, but the blocklist is not a security boundary.

The browser uses a fresh in-memory context, not an existing browser profile. Closing it discards that context; pages and screenshots can still contain sensitive data, and remote sites can retain information sent to them. HTTP(S) requests, including local network addresses, are allowed; this is not a network sandbox. Service workers are blocked and downloads are not accepted. Treat page and screen contents as untrusted data, not instructions. Human confirmation is guidance on top of the enforced consent boundary described above.

`inspect_media` reads metadata, not perceptual quality, and does not play or modify the media. It rejects direct network URLs and UNC paths and restricts ffprobe protocols, but is not a filesystem sandbox: local manifests can refer to other readable files, and mapped drives and filesystem races are not detected.

Game input and screenshots are real-time, subject to latency, focus changes, rendering, and application-specific input handling. They are not deterministic simulation or proof of a game's state. For reproducible game tests prefer engine-level instrumentation and deterministic test fixtures; verify observed outcomes separately.

## Architecture

`istar_control.server` exposes FastMCP tools over stdio, plus `istar-control://guide` and the `computer_use_workflow` prompt. Desktop operations run in worker threads behind a lock; `desktop.py` uses Win32 `SendInput` and Pillow screen capture. Every desktop/capture action passes through `guard.py` (consent, TTL, process-identity checks, session/minute budgets, emergency stop and JSONL audit). `wgc.py` uses an optional exact-HWND capture worker with a deadline. Browser operations share a lock and an owned Playwright Chromium session, which is closed on server shutdown. `media.py` invokes ffprobe/ffmpeg with a timeout and bounded output, and exposes coarse `compare_frames` and `audio_profile` helpers in addition to metadata probes. `bridge.py` publishes the local activity status. `uia.py` performs window-scoped, foreground-checked UI Automation with hard wall-clock timeouts; password controls are omitted and their descendants are never enumerated. `uia_set_value` is write-only and never reads or logs values. OCR and existing-profile browser access are not implemented.

### Godot fixture harness

`godot/tick_harness.gd` runs an explicitly supplied, trusted project fixture implementing `setup(seed)`, `step(tick, actions)` and `snapshot()`. Its synchronous steps are **not** Godot physics frames and require fixture-owned fixed-step logic. It does not instrument existing games automatically, guarantee cross-platform determinism, or sandbox project code. The script is currently a source-tree test utility, not an MCP tool or part of the wheel. `tests/test_godot.py` demonstrates the contract.

## Tests

After installation, from `S:\Proyectos\Istar-Ai-Utils\MCPs\Istar-Control`:

```powershell
uv run python -m compileall -q istar_control
uv run --extra test --extra wgc python -m pytest
```

Test results depend on the environment; passing tests do not prove real desktop input, browser access, or application outcomes on a particular machine.
Set `GODOT_BIN` to a Godot 4 console executable to enable the isolated fixture integration tests.

## Research and remaining work

These are references and possible future directions, not features promised by this server:

- Windows UI Automation is foreground-checked, bounded and timeout-guarded (`timeout` 1–60 s, default 10 s) but remains limited to one matching non-password control per action. Microsoft's [UFO](https://github.com/microsoft/UFO) combines UIA, Win32 and visual techniques; its [repository license](https://github.com/microsoft/UFO/blob/main/LICENSE) is MIT.
- Audit logs currently store action name, handle, outcome, capabilities/TTL metadata and an opaque session id; they do not store typed text, screenshots or credentials. Reviewers wanting captions for screen content for accessibility should use `uia_observe` before actions.
- Exact-HWND WGC uses optional `windows-capture`; native ARM64 support is not established. [Computer Control MCP](https://github.com/AB498/computer-control-mcp) is MIT licensed and uses title-based selection. Microsoft's [Win32 capture sample](https://github.com/robmikh/Win32CaptureSample) demonstrates `CreateForWindow`; WGC does not guarantee protected content.
- Compare [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp), licensed [Apache-2.0](https://github.com/microsoft/playwright-mcp/blob/main/LICENSE), with the proprietary model/API services described in [OpenAI computer use](https://platform.openai.com/docs/guides/tools-computer-use) and [Anthropic computer use](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/computer-use-tool). Their sample code may carry separate licenses; do not conflate those with model availability or licensing. This project uses the Playwright Python library, not Microsoft's MCP server.
- Frame extraction and sample-level audio statistics do not establish perceptual or semantic media quality. The fixture harness does not control a running game.
- For an OpenCode-native experience, the companion plugin in `Plugins/istar-control-panel` provides a read-only status surface over [plugin RPC](https://opencode.ai/v2/docs/build/plugins/rpc). It cannot approve, block or stop desktop input; its TUI part loads automatically from the `opencode.json` plugin declaration, and it has not been exercised in a real TUI terminal yet. Session model selection and configuration changes remain separate operations requiring appropriate consent.
- OpenAI's public [Computer Use documentation](https://developers.openai.com/codex/computer-use) describes foreground-only Windows operation and user takeover. The original indicator here is not a reproduction: Codex demo frames were not visually verified.

## Verified behaviour on this machine

- The MCP server was driven over stdio against the real Godot window `Istar SM63 (DEBUG)`: exact-HWND WGC captures before and after holding the right arrow show the character moving, and `GetAsyncKeyState` reports the key released afterwards. Images: `%LOCALAPPDATA%\Temp\opencode\istar-e2e-before.png` and `istar-e2e-after.png`.
- The browser tools were exercised against a local test page over the real MCP transport: open, fill, click, ARIA snapshot, key press, scroll, back and close all succeeded.
- `desktop_capture_window` returned the accelerated game window without any overlapping window content.
- The Godot fixture harness was executed with the installed Godot 4.7.2 console binary on an isolated temporary project.
