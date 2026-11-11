# Istar-Control vs. hosted computer use (OpenAI Codex / Claude)

As of November 2026. Vendor features change; dates and names below refer to the
documentation fetched for this comparison. Verify current availability per model
and plan before relying on any vendor capability.

## At a glance

| Dimension | Istar-Control (this project) | OpenAI Codex Computer Use | Anthropic computer use |
| --- | --- | --- | --- |
| Form factor | Open-source MCP server for OpenCode | Built into the ChatGPT desktop app (macOS/Windows); also the OpenAI Responses API (`tools: [{type:"computer"}]`) | Client-executed toolset (`computer_toolset_20260801`) inside the Claude API; the app runs the environment |
| Environment | The user's live Windows desktop, no sandbox or VM | Local app on the target desktop; Windows is **foreground-only**; macOS can be background or locked-use | Reference environment is a Docker VM (X11/Mutter/Tint2, Xvfb); client-side, the environment is yours to choose |
| Control model | Physical `SendInput` keyboard/mouse + Win32 capture (screen crop & WGC) | Vendor-driven clicks, pointer, typing, per published screenshots | 17 member tools: screenshot, zoom, clicks, drag, scroll, type, key, hold_key, cursor_position, wait |
| Approvals | Local grant dialog scoped to HWND + process, TTL, capability (`focus`/`capture`/`input`), budgets, emergency stop | Per-app approvals with "Always allow" list; workspace admin policy; Codex takes over the foreground on Windows | Human reviews per tool call via application; system prompt, batch-halt semantics; your app owns enforcement |
| Verification | Audit JSONL (never typed text), grants, `control_status` RPC; indicator overlay is best-effort advisory only | ChatGPT data controls apply; foreground takeover; no publicly documented per-action audit log locally | Application owns audit; Claude batches halt at the first failed action and mark later members `"Not executed: an earlier computer action in this turn failed."` |
| Determinism for games | Godot source-tree fixture harness; pointer/keyboard actions are timed, not game-locked | Designed for general desktop tasks; foreground-only on Windows | Designed for sandboxed desktop orchestration |
| Browser | Ephemeral owned Chromium over HTTP(S); no profiles, blocked downloads/service workers | ChatGPT desktop browser (signed-in sites reachable); Responses API can drive a browser with Playwright | You provide Playwright; Anthropic's SDK browser toolset + this computer tool
| Cost | Free (hardware you own) | ChatGPT subscription or API tokens | API token spend plus your environment |
| License | This project's repository | Proprietary, OpenAI | Anthropic-defined client toolset; reference demo quickstart is open source |

## Deep dive

### Consent and containment

- **Istar-Control** implements consent as a local grant: the window title,
  process and requested capabilities are shown in a Windows dialog. Denial,
  timeout or error means no grant. Grants are HWND- and process-scoped, expire,
  and can be revoked at any time (`control_stop`). Session and per-minute
  budgets bound unattended operation, and `audit.jsonl` records action, handle
  and outcome without typed text. It is still a single-user live desktop:
  identity proofs can be faked by local processes, so grants are not a
  cryptographic boundary, and focus races remain a documented risk.
- **Codex** relies on OS-level and ChatGPT-level permissions: on macOS,
  Screen Recording and Accessibility; on Windows, the app runs on the active
  desktop (foreground). Per-app "Always allow" reuses approvals across tasks.
  No locally visible per-action audit log is documented; the permission model
  is enforced by the product, not user-removable.
- **Anthropic** requires the caller to build the environment, the agent loop and
  any consent UI. The tool results may include prompt-injection classifiers on
  `tool_result` images. Because the toolset is client-executed, your app can
  add any consent that fits the task, or nothing at all
  (e.g., an unattended server). The reference quickstart uses a disposable
  Docker VM, which is the strongest isolation design of the three, but no
  particular sandbox is shipped in the toolset itself.

### Latency and feedback loops

- Every computer-use agent waits on a screenshot/`tool_result` round trip.
  Codex on Windows and Claude in your loop are comparable in practice:
  one action at a time, verify, then next action. Istar-Control has no
  turn-of-engine: its tools are synchronous, deterministic, and fast, but the
  **model is not built in**; any client (OpenCode) decides the loop.
- Claude returns several member tools per turn ("batch actions"); run them in
  order, halt on the first failure. Codex exposes the loop through the
  Responses API; early examples bound it at 20 responses. Neither product
  exposes an open protocol like MCP: their loops are implementation details.
- Istar-Control keeps the model-free decision local, which is good for bounded
  fixture tests (see `godot/tick_harness.gd`) and wrong for open-ended tasks:
  somebody must still write the prompt/loop, and the MCP returns tool results,
  not goals.

### Where each one is the right call

- **Istar-Control** — Windows desktops you already own, engineering tasks that
  need Win32-specific capture (WGC), local media tooling, Godot fixture
  harnesses, compliance-friendly audit logs, or workflows that must run without
  vendor cloud APIs. It is the only option without vendor lock-in and the only
  one with enforced stop/budget controls you can audit, but it is the least
  turnkey.
- **Codex Computer Use** — delegate-and-watch: desktop tasks on a user's Mac
  or Windows machine with vendor support, per-app allowlists, and the Codex
  model stack. Windows foreground-only, no useful audit locally, and you accept
  ChatGPT as the operator.
- **Anthropic computer use** — custom runtime integration with full control of
  the loop, your own sandbox (Docker recommended), and Claude models; best for
  agent systems you already operate where tool consent is part of the design
  and you want batch action ordering "stop at first failure" semantics built
  into the protocol.

### Known limitations and open gaps (honest)

- None of the three isolates a misbehaving model. Istar-Control is on the live
  desktop **by design**; Codex is foreground-only on Windows; Claude needs you
  to choose and harden the environment.
- Windows window handles can be recycled; Istar-Control mitigates with
  process-identity checks, but a handle-purchase race remains inherent to
  HWND-based selection.
- WGC can return blank images for protected/accelerated content; Media tests do
  not vet UI correctness.
- The public claims in this document ("foreground-only", "17 member tools",
  "toolset 20260801", "GPT-6 Astra") reflect vendor documentation as of
  November 2026 and may differ in later releases; none of them was exercised
  end-to-end here — Istar-Control is the only system that runs on this machine.
