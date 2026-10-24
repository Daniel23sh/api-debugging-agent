import { describe, expect, it, vi } from 'vitest';
import { clearHistory, HISTORY_KEY, loadHistory, saveHistory } from './history';
import { savedFixture } from './test/fixtures';

describe('Browser-local history', () => {
  it('starts empty without writing storage', () => {
    const write = vi.spyOn(Storage.prototype, 'setItem');
    expect(loadHistory()).toEqual({ entries: [], notice: null });
    expect(write).not.toHaveBeenCalled();
  });

  it.each(['RESOLVED', 'NO_BUG_FOUND', 'INCONCLUSIVE', 'LIMIT_REACHED'] as const)('round trips a complete %s snapshot', (status) => {
    const entry = savedFixture(1, status);
    entry.response.trace_id = '0123456789abcdef0123456789abcdef';
    entry.response.metrics = { duration_seconds: 2, tool_attempt_count: 6, model_request_count: 1, input_tokens: 20, output_tokens: 10, total_tokens: 30 };
    expect(saveHistory([entry])).toBeNull();
    expect(loadHistory()).toEqual({ entries: [entry], notice: null });
    expect(JSON.parse(localStorage.getItem(HISTORY_KEY)!)).toEqual({ version: 1, entries: [entry] });
  });

  it('retains five newest entries and keeps separate runs of the same report', () => {
    const entries = Array.from({ length: 6 }, (_, index) => ({ ...savedFixture(6 - index), report: 'Identical report' }));
    saveHistory(entries);
    expect(loadHistory().entries.map((entry) => entry.id)).toEqual(entries.slice(0, 5).map((entry) => entry.id));
    expect(JSON.parse(localStorage.getItem(HISTORY_KEY)!).entries).toHaveLength(5);
  });

  it('keeps valid entries, orders by completion time, and ignores malformed or duplicate records', () => {
    const entry = savedFixture();
    localStorage.setItem(HISTORY_KEY, JSON.stringify({ version: 1, entries: [entry, { ...entry, report: '' }, { ...savedFixture(2), response: { ...entry.response, trace_id: 'PRIVATE-invalid' } }, entry, savedFixture(3)] }));
    const result = loadHistory();
    expect(result.entries).toEqual([savedFixture(3), entry]);
    expect(result.notice).toMatch(/Invalid saved investigations/);
    expect(result.notice).not.toContain('PRIVATE');
  });

  it.each([
    '{PRIVATE-invalid-json',
    JSON.stringify({ version: 2, entries: [savedFixture()] }),
    JSON.stringify({ entries: 'PRIVATE-invalid' }),
  ])('ignores unsupported storage without rewriting it', (raw) => {
    localStorage.setItem(HISTORY_KEY, raw);
    const result = loadHistory();
    expect(result.entries).toEqual([]);
    expect(result.notice).toBeTruthy();
    expect(result.notice).not.toContain('PRIVATE');
    expect(localStorage.getItem(HISTORY_KEY)).toBe(raw);
  });

  it('rejects private extra fields, invalid report lengths and invalid dates', () => {
    const entry = savedFixture();
    localStorage.setItem(HISTORY_KEY, JSON.stringify({ version: 1, entries: [
      { ...entry, credentials: 'PRIVATE' }, { ...entry, completedAt: 'not-a-date' },
      { ...entry, report: '😀'.repeat(4001) },
    ] }));
    expect(loadHistory().entries).toEqual([]);
    localStorage.setItem(HISTORY_KEY, JSON.stringify({ version: 1, entries: [{ ...entry, report: '😀'.repeat(4000) }] }));
    expect(loadHistory().entries).toHaveLength(1);
  });

  it('handles blocked reads and quota failures without exposing exception details', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('PRIVATE'); });
    expect(loadHistory()).toEqual({ entries: [], notice: expect.stringMatching(/could not be read/) });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('PRIVATE', 'QuotaExceededError'); });
    const notice = saveHistory([savedFixture()]);
    expect(notice).toMatch(/will not survive a reload/);
    expect(notice).not.toContain('PRIVATE');
  });

  it('clears only its own key and reports failed removal', () => {
    saveHistory([savedFixture()]);
    localStorage.setItem('other-app', 'preserve');
    expect(clearHistory()).toBeNull();
    expect(localStorage.getItem(HISTORY_KEY)).toBeNull();
    expect(localStorage.getItem('other-app')).toBe('preserve');
    saveHistory([savedFixture()]);
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('PRIVATE'); });
    expect(clearHistory()).toMatch(/Stored copies may still remain/);
    expect(loadHistory().entries).toHaveLength(1);
  });
});
