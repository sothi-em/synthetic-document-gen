/**
 * In-memory store of proposed destructive actions.
 *
 * Destructive tools never call their endpoint directly: they propose an
 * action, the agent shows the user the summary, and only after explicit
 * user agreement does `confirm_action` consume the id and execute it.
 */

import { randomUUID } from "node:crypto";

export interface PendingAction {
  method: string;
  path: string;
  body?: unknown;
  /** Plain-language description shown to the user. */
  summary: string;
  createdAt: number;
}

const TTL_MS = 5 * 60 * 1000;

const pending = new Map<string, PendingAction>();

/** Store a proposed action and return its confirmation id. */
export function propose(action: Omit<PendingAction, "createdAt">): string {
  const id = randomUUID();
  pending.set(id, { ...action, createdAt: Date.now() });
  return id;
}

/**
 * Consume a proposed action by id.
 *
 * Returns the stored action (and removes it) when the id is known and
 * unexpired; returns `null` when absent or expired.
 */
export function consume(id: string): PendingAction | null {
  const entry = pending.get(id);
  if (!entry) return null;
  pending.delete(id);
  if (Date.now() - entry.createdAt > TTL_MS) return null;
  return entry;
}
