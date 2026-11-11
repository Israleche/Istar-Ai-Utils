import { Plugin } from "@opencode/plugin"
import { IstarControl } from "./rpc.js"
import { readStatus } from "./status.js"

const POLL_INTERVAL_MS = 2000

/**
 * Server part of the Istar-Control companion surfaces.
 *
 * It reads the local status file published by the Istar-Control MCP server and
 * exposes it read-only over plugin RPC. It opens no network port, injects no
 * input and cannot grant or revoke authority: the MCP server remains the only
 * place where actions are performed.
 */
export default Plugin.define({
  id: "opencode-istar-control-panel",
  async setup(ctx) {
    let previous = ""

    const registration = await ctx.rpc.register(IstarControl, {
      status: async () => readStatus(),
    })

    const timer = setInterval(() => {
      const status = readStatus()
      const signature = `${status.state}:${status.action ?? ""}:${status.title ?? ""}`
      if (signature === previous) return
      previous = signature
      registration.events
        .emit("updated", {
          state: status.state,
          action: status.action,
          title: status.title,
        })
        .catch(() => {
          // Event delivery is best effort; clients resynchronise on demand.
        })
    }, POLL_INTERVAL_MS)

    return () => {
      clearInterval(timer)
      void registration.dispose()
    }
  },
})
