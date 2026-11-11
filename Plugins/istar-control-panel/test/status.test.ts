import assert from "node:assert/strict"
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import { describe, readStatus, statusPath } from "../src/status.ts"

function withStatusFile(contents: string | null): () => void {
  const directory = mkdtempSync(join(tmpdir(), "istar-control-status-"))
  const path = join(directory, "status.json")
  process.env.ISTAR_CONTROL_STATUS_PATH = path
  if (contents !== null) writeFileSync(path, contents, "utf8")
  return () => {
    delete process.env.ISTAR_CONTROL_STATUS_PATH
  }
}

test("missing status file reads as idle", () => {
  const restore = withStatusFile(null)
  try {
    const status = readStatus()
    assert.equal(status.state, "idle")
    assert.equal(status.stale, true)
  } finally {
    restore()
  }
})

test("published status is read with an age", () => {
  const now = 1_000_000
  const restore = withStatusFile(
    JSON.stringify({
      server: "Istar-Control",
      state: "active",
      action: "click",
      operation: "click",
      hwnd: 42,
      title: "Istar SM63 (DEBUG)",
      pid: 7,
      updated_at: now - 5,
    }),
  )
  try {
    const status = readStatus(now)
    assert.equal(status.state, "active")
    assert.equal(status.hwnd, 42)
    assert.equal(status.title, "Istar SM63 (DEBUG)")
    assert.equal(status.stale, false)
    assert.equal(status.age_seconds, 5)
  } finally {
    restore()
  }
})

test("malformed json reads as idle", () => {
  const restore = withStatusFile("{not json")
  try {
    assert.equal(readStatus().state, "idle")
  } finally {
    restore()
  }
})

test("old status is reported as stale", () => {
  const restore = withStatusFile(
    JSON.stringify({ server: "Istar-Control", state: "active", updated_at: 1 }),
  )
  try {
    const status = readStatus(100_000)
    assert.equal(status.stale, true)
  } finally {
    restore()
  }
})

test("describe returns a short summary", () => {
  const restore = withStatusFile(
    JSON.stringify({
      server: "Istar-Control",
      state: "completed",
      action: "key",
      title: "Istar SM63 (DEBUG)",
      updated_at: Date.now() / 1000,
    }),
  )
  try {
    const text = describe(readStatus())
    assert.match(text, /Istar-Control: completed key/)
    assert.match(text, /Istar SM63 \(DEBUG\)/)
  } finally {
    restore()
  }
})

test("status path honours the environment override", () => {
  const restore = withStatusFile(null)
  try {
    assert.equal(statusPath(), process.env.ISTAR_CONTROL_STATUS_PATH)
  } finally {
    restore()
  }
})
