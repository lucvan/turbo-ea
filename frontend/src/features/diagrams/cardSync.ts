/**
 * The `POST /cards` body for a card created on the canvas.
 *
 * Both ways of pushing a pending card to the inventory — syncing that one
 * card and **Sync all** — build it here, so they cannot drift. They did: each
 * sent the type and the name alone, so the description typed in the
 * Create-card dialog never reached the card (#1210).
 */
import type { ScannedPendingFS } from "./drawio-shapes";

export function cardCreatePayload(
  item: Pick<ScannedPendingFS, "type" | "name" | "description">,
): Record<string, unknown> {
  const payload: Record<string, unknown> = { type: item.type, name: item.name };
  // `CardCreate.description` is `str | None`: a blank is "none", not "".
  const description = item.description?.trim();
  if (description) payload.description = description;
  return payload;
}
