import { Rpc } from "@opencode/plugin/rpc"

/**
 * Read-only view of the local Istar-Control activity status.
 *
 * The contract deliberately exposes no method that injects input, approves an
 * action or grants authority. It is an observer, not a control surface.
 */
const statusOutput = {
  type: "object",
  properties: {
    server: { type: "string" },
    state: {
      type: "string",
      enum: ["active", "completed", "failed", "idle", "unknown"],
    },
    action: { type: ["string", "null"] },
    operation: { type: ["string", "null"] },
    hwnd: { type: ["number", "null"] },
    title: { type: ["string", "null"] },
    updated_at: { type: ["number", "null"] },
    pid: { type: ["number", "null"] },
    age_seconds: { type: ["number", "null"] },
    stale: { type: "boolean" },
  },
  required: [
    "server",
    "state",
    "action",
    "operation",
    "hwnd",
    "title",
    "updated_at",
    "pid",
    "age_seconds",
    "stale",
  ],
  additionalProperties: false,
} as const

const statusInput = {
  type: "object",
  properties: {},
  additionalProperties: false,
} as const

const updatedEvent = {
  type: "object",
  properties: {
    state: { type: "string" },
    action: { type: ["string", "null"] },
    title: { type: ["string", "null"] },
  },
  required: ["state", "action", "title"],
  additionalProperties: false,
} as const

export const IstarControl = Rpc.define({
  id: "istar-control",
  methods: {
    status: {
      input: statusInput,
      output: statusOutput,
    },
  },
  events: {
    updated: {
      schema: updatedEvent,
    },
  },
})
