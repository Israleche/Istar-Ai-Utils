import { Plugin } from "@opencode/plugin/tui"
import { Show, createSignal, onCleanup } from "solid-js"
import { IstarControl } from "./rpc.js"
import { describe, readStatus, type ControlStatus } from "./status.js"

const PANEL_NAME = "istar-control.activity"
const REFRESH_INTERVAL_MS = 2000

/**
 * Terminal surfaces for the local Istar-Control MCP server.
 *
 * The panel reports what the MCP server is controlling. It is an observer:
 * it does not approve actions, does not inject input and cannot stop the MCP
 * server. An emergency stop is not implemented.
 */
export default Plugin.define({
  id: "opencode-istar-control-panel-tui",
  setup(context) {
    const [status, setStatus] = createSignal<ControlStatus>(readStatus())

    // The status file is the primary source: it is written by the MCP server
    // and always readable, even when the plugin RPC is not reachable.
    const refresh = async () => {
      setStatus(readStatus())
      try {
        const result = await context.client.rpc(IstarControl).status({})
        setStatus(result as ControlStatus)
      } catch {
        // The RPC is an optional refinement; the local file remains valid.
      }
    }

    const timer = setInterval(() => void refresh(), REFRESH_INTERVAL_MS)
    const unsubscribe = context.client
      .rpc(IstarControl)
      .events.on("updated", () => void refresh())
    onCleanup(() => {
      clearInterval(timer)
      unsubscribe()
    })

    context.keymap.layer(() => ({
      mode: "global",
      commands: [
        {
          id: "istar-control.activity.toggle",
          title: "Toggle Istar-Control activity",
          bind: "ctrl+alt+i",
          palette: true,
          run: () => {
            context.ui.panel.open(PANEL_NAME)
          },
        },
        {
          id: "istar-control.activity.status",
          title: "Show Istar-Control status",
          run: () => {
            context.ui.toast.show({
              title: "Istar-Control",
              message: describe(status()),
              variant: status().stale ? "info" : "success",
            })
          },
        },
      ],
    }))

    context.ui.slot({
      append: "home.footer.status",
      render: () => <text fg={context.theme.text.muted}>{describe(status())}</text>,
    })

    context.ui.slot({
      append: "session.panel",
      render: (panel) => (
        <Show when={panel.name === PANEL_NAME}>
          <box flexDirection="column" padding={1}>
            <text fg={context.theme.text.base}>Istar-Control activity</text>
            <text fg={context.theme.text.muted}>{describe(status())}</text>
            <Show when={status().stale}>
              <text fg={context.theme.text.muted}>No recent activity reported.</text>
            </Show>
            <text fg={context.theme.text.muted}>
              Observer only: this panel cannot approve, block or stop desktop input.
            </text>
          </box>
        </Show>
      ),
    })
  },
})
