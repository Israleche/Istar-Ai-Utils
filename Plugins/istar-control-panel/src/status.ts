import { homedir } from "node:os"
import { join } from "node:path"
import { readFileSync, statSync } from "node:fs"

/**
 * Local activity status published by the Istar-Control MCP server.
 *
 * The file is a status hint, not an authorization channel: any local process
 * can write it. It never carries typed text, screenshots or credentials.
 */
export interface ControlStatus {
  server: string
  state: "active" | "completed" | "failed" | "idle" | "unknown"
  action: string | null
  operation: string | null
  hwnd: number | null
  title: string | null
  updated_at: number | null
  pid: number | null
  /** Age of the status in seconds, or null when it is unknown. */
  age_seconds: number | null
  /** True when the status is too old to describe current activity. */
  stale: boolean
}

const MAX_BYTES = 64 * 1024
const STALE_AFTER_SECONDS = 120

export function statusPath(): string {
  const override = process.env.ISTAR_CONTROL_STATUS_PATH
  if (override) return override
  const base = process.env.XDG_DATA_HOME
  const root = base ? base : join(homedir(), ".local", "share")
  return join(root, "opencode", "istar-control", "status.json")
}

function idleStatus(): ControlStatus {
  return {
    server: "Istar-Control",
    state: "idle",
    action: null,
    operation: null,
    hwnd: null,
    title: null,
    updated_at: null,
    pid: null,
    age_seconds: null,
    stale: true,
  }
}

/** Read the published status. Missing, unreadable or malformed files are idle. */
export function readStatus(now: number = Date.now() / 1000): ControlStatus {
  let raw: string
  try {
    const info = statSync(statusPath())
    if (info.size > MAX_BYTES) return idleStatus()
    raw = readFileSync(statusPath(), "utf8")
  } catch {
    return idleStatus()
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return idleStatus()
  }
  if (typeof parsed !== "object" || parsed === null) return idleStatus()
  const data = parsed as Record<string, unknown>
  const updated = typeof data.updated_at === "number" ? data.updated_at : null
  const age = updated === null ? null : Math.max(0, now - updated)
  const state = data.state
  const known = state === "active" || state === "completed" || state === "failed" || state === "idle"
  return {
    server: typeof data.server === "string" ? data.server : "Istar-Control",
    state: known ? state : "unknown",
    action: typeof data.action === "string" ? data.action : null,
    operation: typeof data.operation === "string" ? data.operation : null,
    hwnd: typeof data.hwnd === "number" ? data.hwnd : null,
    title: typeof data.title === "string" ? data.title : null,
    updated_at: updated,
    pid: typeof data.pid === "number" ? data.pid : null,
    age_seconds: age,
    stale: age === null || age > STALE_AFTER_SECONDS,
  }
}

/** One-line summary for a status bar. */
export function describe(status: ControlStatus): string {
  if (status.stale) return "Istar-Control: idle"
  const target = status.title ? ` ${status.title}` : status.hwnd ? ` hwnd ${status.hwnd}` : ""
  const action = status.operation ? `${status.action}/${status.operation}` : status.action
  return `Istar-Control: ${status.state} ${action ?? "control"}${target}`
}
