import { z } from 'zod';
import { demoResponseSchema, issueLength, MAX_ISSUE_CHARS, scenarioIdSchema } from './api';

export const HISTORY_KEY = 'apilens.recent-investigations.v1';
export const HISTORY_LIMIT = 5;

export const savedInvestigationSchema = z.strictObject({
  id: z.uuid(),
  completedAt: z.iso.datetime(),
  report: z.string().trim().refine((value) => issueLength(value) > 0 && issueLength(value) <= MAX_ISSUE_CHARS),
  scenarioId: scenarioIdSchema.nullable(),
  response: demoResponseSchema,
});
export type SavedInvestigation = z.infer<typeof savedInvestigationSchema>;
export type RecentHistory = { entries: SavedInvestigation[]; notice: string | null };

const collectionSchema = z.strictObject({ version: z.literal(1), entries: z.array(z.unknown()) });

export function loadHistory(): RecentHistory {
  let raw: string | null;
  try { raw = window.localStorage.getItem(HISTORY_KEY); }
  catch { return { entries: [], notice: 'Browser storage could not be read. Recent investigations may not survive a reload.' }; }
  if (raw === null) return { entries: [], notice: null };
  let value: unknown;
  try { value = JSON.parse(raw); }
  catch { return { entries: [], notice: 'Saved history could not be read and was ignored.' }; }
  const collection = collectionSchema.safeParse(value);
  if (!collection.success) return { entries: [], notice: 'Saved history has an unsupported format or version and was not loaded.' };
  const entries: SavedInvestigation[] = [];
  const ids = new Set<string>();
  let discarded = false;
  for (const item of collection.data.entries) {
    const entry = savedInvestigationSchema.safeParse(item);
    if (!entry.success || ids.has(entry.data.id)) { discarded = true; continue; }
    ids.add(entry.data.id);
    entries.push(entry.data);
  }
  entries.sort((a, b) => Date.parse(b.completedAt) - Date.parse(a.completedAt));
  return { entries: entries.slice(0, HISTORY_LIMIT), notice: discarded ? 'Invalid saved investigations were ignored. Valid records remain available.' : null };
}

export function saveHistory(entries: SavedInvestigation[]): string | null {
  try {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify({ version: 1, entries: entries.slice(0, HISTORY_LIMIT) }));
    return null;
  } catch { return 'Recent investigations are available in memory, but could not be saved. New records will not survive a reload.'; }
}

export function clearHistory(): string | null {
  try { window.localStorage.removeItem(HISTORY_KEY); return null; }
  catch { return 'Saved history could not be removed. Stored copies may still remain in this browser.'; }
}
