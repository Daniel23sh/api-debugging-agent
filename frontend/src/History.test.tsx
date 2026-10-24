import { StrictMode } from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import App from './App';
import { HISTORY_KEY, loadHistory, saveHistory } from './history';
import { jsonResponse, observedSteps, responseFixture, savedFixture } from './test/fixtures';
import { scenarios } from './scenarios';

const report = () => screen.getByRole('textbox', { name: /Bug report/ });
const cost = () => screen.getByRole('checkbox', { name: /may incur API costs/ });
const start = () => screen.getByRole('button', { name: /Start investigation/ });
const recent = () => screen.getByRole('region', { name: 'Recent investigations' });
const savedButton = (index = 1) => within(recent()).getByRole('button', { name: new RegExp(`Saved report ${index}`) });
async function revealHistory(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByText(/^Recent investigations \(/));
}
async function confirmClear(user: ReturnType<typeof userEvent.setup>) {
  await user.click(within(screen.getByRole('dialog', { name: 'Clear saved investigations?' })).getByRole('button', { name: 'Delete saved history' }));
}

describe('Recent investigations workspace', () => {
  it('saves once under StrictMode, retains identical runs, evicts the sixth, and restores after remount without requests', async () => {
    const write = vi.spyOn(Storage.prototype, 'setItem');
    const user = userEvent.setup();
    const mounted = render(<StrictMode><App /></StrictMode>);
    await user.type(report(), 'Repeated report');
    await user.click(cost());
    for (let index = 0; index < 6; index++) {
      vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture('RESOLVED', observedSteps)));
      await user.click(start());
      await screen.findByText('RESOLVED', { selector: '.diagnosis-status code' });
      await user.click(screen.getByRole('link', { name: /Edit report \/ run again/ }));
    }
    const entries = loadHistory().entries;
    expect(entries).toHaveLength(5);
    expect(new Set(entries.map((entry) => entry.id)).size).toBe(5);
    expect(entries.every((entry) => entry.report === 'Repeated report')).toBe(true);
    expect(write).toHaveBeenCalledTimes(6);
    expect(Object.keys(entries[0]).sort()).toEqual(['completedAt', 'id', 'report', 'response', 'scenarioId']);
    mounted.unmount();
    vi.mocked(fetch).mockClear();
    render(<App />);
    expect(report()).toHaveValue('');
    expect(cost()).not.toBeChecked();
    expect(screen.queryByText('RESOLVED', { selector: '.diagnosis-status code' })).not.toBeInTheDocument();
    await revealHistory(user);
    await user.click(within(recent()).getAllByRole('button', { name: /Repeated report/ })[0]);
    expect(screen.getByText('Saved investigation', { selector: '.panel-caption' })).toBeVisible();
    expect(screen.getByRole('region', { name: 'Investigation outcome' })).toHaveFocus();
    expect(fetch).not.toHaveBeenCalled();
    expect(write).toHaveBeenCalledTimes(6);
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('preserves scenario, free-form draft, and consent while opening saved snapshots and returning through either action', async () => {
    saveHistory([savedFixture()]);
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: /Wrong status/ }));
    await user.click(cost());
    await revealHistory(user);
    await user.click(savedButton());
    await user.click(screen.getByRole('link', { name: /Return to draft/ }));
    expect(report()).toHaveFocus();
    expect(report()).toHaveValue(scenarios[1].issue);
    expect(screen.getByRole('button', { name: /Wrong status/ })).toHaveAttribute('aria-pressed', 'true');
    expect(cost()).toBeChecked();
    await user.type(report(), ' My unfinished change.');
    await user.click(savedButton());
    await user.click(screen.getByText('Return to draft', { selector: '.report-editor summary span' }));
    expect(report()).toHaveFocus();
    expect(report()).toHaveValue(`${scenarios[1].issue} My unfinished change.`);
    expect(screen.getByText('Free-form')).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
    expect(loadHistory().entries).toEqual([savedFixture()]);
  });

  it('closes a manually expanded live report when its saved snapshot is opened', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture('RESOLVED')));
    const user = userEvent.setup();
    render(<App />);
    await user.type(report(), 'Expanded live report');
    await user.click(cost());
    await user.click(start());
    await user.click(await screen.findByText('Report & investigation controls'));
    expect(report()).toBeVisible();
    await user.click(screen.getByRole('link', { name: 'Recent (1)' }));
    await user.click(within(recent()).getByRole('button', { name: /Expanded live report/ }));
    expect(document.querySelector('.report-editor')).not.toHaveAttribute('open');
    expect(report()).not.toBeVisible();
    expect(document.querySelectorAll('.report-editor')).toHaveLength(1);
    await user.click(screen.getByRole('link', { name: /Return to draft/ }));
    expect(document.querySelectorAll('.report-editor')).toHaveLength(1);
    expect(report()).toHaveValue('Expanded live report');
    expect(report()).toHaveFocus();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('restores exact response data, resets disclosures between records, and keeps Phoenix lazy', async () => {
    const first = savedFixture();
    first.response.trace_id = '0123456789abcdef0123456789abcdef';
    first.response.metrics = { duration_seconds: 3.25, tool_attempt_count: 6, model_request_count: 2, input_tokens: 10, output_tokens: 2, total_tokens: 12 };
    const second = { ...savedFixture(2, 'LIMIT_REACHED'), response: { ...responseFixture('LIMIT_REACHED', observedSteps), trace_id: first.response.trace_id } };
    saveHistory([second, first]);
    const user = userEvent.setup();
    render(<App />);
    await revealHistory(user);
    await user.click(savedButton());
    await user.click(screen.getByRole('link', { name: 'Evidence (1)' }));
    await user.click(screen.getByRole('link', { name: 'Timeline (6)' }));
    expect(screen.getByText('3.25 s')).toBeVisible();
    expect(screen.getByRole('link', { name: /Open in Phoenix/ })).toHaveAttribute('href', `http://127.0.0.1:6006/redirects/traces/${first.response.trace_id}`);
    const steps = within(screen.getByRole('list', { name: 'Recorded investigation steps' })).getAllByRole('listitem');
    expect(steps[0]).toHaveTextContent('HTTP 500');
    expect(steps[1]).toHaveTextContent('Tool failed');
    await user.click(savedButton(2));
    expect(screen.getByText('LIMIT_REACHED', { selector: '.diagnosis-status code' })).toBeVisible();
    expect(document.querySelector('.timeline-panel')).not.toHaveAttribute('open');
    await user.click(savedButton());
    expect(document.querySelector('.evidence-disclosure')).not.toHaveAttribute('open');
    expect(document.querySelector('.timeline-panel')).not.toHaveAttribute('open');
    expect(document.querySelector('iframe')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('disables history selection and clearing during a request and captures the submitted scenario', async () => {
    saveHistory([savedFixture()]);
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const user = userEvent.setup();
    render(<App />);
    await revealHistory(user);
    await user.click(screen.getByRole('button', { name: /Response schema/ }));
    await user.click(cost());
    await user.click(start());
    expect(savedButton()).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Clear history' })).toBeDisabled();
    await user.click(savedButton());
    await act(async () => finish(jsonResponse(responseFixture('INCONCLUSIVE'))));
    expect(loadHistory().entries[0]).toMatchObject({ report: scenarios[3].issue, scenarioId: 'response-schema' });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it.each(['http', 'network', 'malformed'])('does not save a %s failure or lose previous history', async (failure) => {
    saveHistory([savedFixture()]);
    const write = vi.spyOn(Storage.prototype, 'setItem');
    if (failure === 'network') vi.mocked(fetch).mockRejectedValueOnce(new Error('PRIVATE'));
    else vi.mocked(fetch).mockResolvedValueOnce(failure === 'http' ? jsonResponse({ detail: 'PRIVATE' }, 502) : jsonResponse({ diagnosis: {} }));
    const user = userEvent.setup();
    render(<App />);
    await user.type(report(), 'A new report');
    await user.click(cost());
    await user.click(start());
    await screen.findByRole('alert');
    expect(loadHistory().entries).toEqual([savedFixture()]);
    expect(write).not.toHaveBeenCalled();
  });

  it('keeps a completed diagnosis and browsable in-memory history when saving fails', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('PRIVATE', 'QuotaExceededError'); });
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture('RESOLVED')));
    const user = userEvent.setup();
    render(<App />);
    await user.type(report(), 'Memory-only report');
    await user.click(cost());
    await user.click(start());
    expect(await screen.findByText('RESOLVED', { selector: '.diagnosis-status code' })).toBeVisible();
    expect(screen.getByText(/New records will not survive a reload/)).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Recent (1)' }));
    await user.click(within(recent()).getByRole('button', { name: /Memory-only report/ }));
    expect(screen.getByText('Saved investigation', { selector: '.panel-caption' })).toBeVisible();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('confirms clearing, removes only its own key, and leaves the displayed snapshot and draft in memory', async () => {
    saveHistory([savedFixture()]);
    localStorage.setItem('other-app', 'preserve');
    const nativeConfirm = vi.spyOn(window, 'confirm');
    const user = userEvent.setup();
    render(<App />);
    await user.type(report(), 'Keep this draft');
    await revealHistory(user);
    await user.click(savedButton());
    await user.click(screen.getByRole('button', { name: 'Clear history' }));
    const dialog = screen.getByRole('dialog', { name: 'Clear saved investigations?' });
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(within(dialog).getByText(/displayed result and unfinished draft will stay open/)).toBeVisible();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(loadHistory().entries).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Clear history' })).toHaveFocus();
    await user.click(screen.getByRole('button', { name: 'Clear history' }));
    await confirmClear(user);
    expect(localStorage.getItem(HISTORY_KEY)).toBeNull();
    expect(localStorage.getItem('other-app')).toBe('preserve');
    expect(screen.getByText('RESOLVED', { selector: '.diagnosis-status code' })).toBeVisible();
    await user.click(screen.getByRole('link', { name: /Return to draft/ }));
    expect(report()).toHaveValue('Keep this draft');
    expect(fetch).not.toHaveBeenCalled();
    expect(nativeConfirm).not.toHaveBeenCalled();
  });

  it.each(['Escape', 'Enter'])('keeps history when cancelling the themed dialog with %s', async (key) => {
    saveHistory([savedFixture()]);
    const remove = vi.spyOn(Storage.prototype, 'removeItem');
    const user = userEvent.setup();
    render(<App />);
    await revealHistory(user);
    const trigger = screen.getByRole('button', { name: 'Clear history' });
    await user.click(trigger);
    const dialog = screen.getByRole('dialog', { name: 'Clear saved investigations?' });
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(remove).not.toHaveBeenCalled();
    if (key === 'Escape') fireEvent(dialog, new Event('cancel', { cancelable: true }));
    else await user.keyboard('{Enter}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(loadHistory().entries).toHaveLength(1);
    expect(remove).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('cycles keyboard focus within the confirmation actions', async () => {
    saveHistory([savedFixture()]);
    const user = userEvent.setup();
    render(<App />);
    await revealHistory(user);
    await user.click(screen.getByRole('button', { name: 'Clear history' }));
    const dialog = screen.getByRole('dialog', { name: 'Clear saved investigations?' });
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' });
    const remove = within(dialog).getByRole('button', { name: 'Delete saved history' });
    await user.keyboard('{Shift>}{Tab}{/Shift}');
    expect(remove).toHaveFocus();
    await user.tab();
    expect(cancel).toHaveFocus();
    await user.tab();
    expect(remove).toHaveFocus();
    await user.keyboard('{Shift>}{Tab}{/Shift}');
    expect(cancel).toHaveFocus();
    expect(loadHistory().entries).toHaveLength(1);
  });

  it('shows failed deletion without claiming that history was cleared', async () => {
    saveHistory([savedFixture()]);
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('PRIVATE'); });
    const user = userEvent.setup();
    render(<App />);
    await revealHistory(user);
    await user.click(screen.getByRole('button', { name: 'Clear history' }));
    await confirmClear(user);
    expect(screen.getByText(/Stored copies may still remain/)).toBeVisible();
    expect(savedButton()).toBeVisible();
    expect(screen.queryByText('PRIVATE')).not.toBeInTheDocument();
  });

  it.each(['{bad-json', JSON.stringify({ version: 2, entries: [savedFixture()] }), JSON.stringify({ version: 1, entries: [{}] })])('allows explicit deletion of unreadable history: %s', async (raw) => {
    localStorage.setItem(HISTORY_KEY, raw);
    localStorage.setItem('other-app', 'preserve');
    const user = userEvent.setup();
    render(<App />);
    await revealHistory(user);
    const clear = screen.getByRole('button', { name: 'Clear history' });
    expect(clear).toBeEnabled();
    await user.click(clear);
    await confirmClear(user);
    expect(localStorage.getItem(HISTORY_KEY)).toBeNull();
    expect(localStorage.getItem('other-app')).toBe('preserve');
    expect(document.getElementById('recent-summary')).toHaveFocus();
    expect(clear).toBeDisabled();
  });

  it('renders saved reports and model text as text, including after blocked storage reads', async () => {
    const entry = savedFixture();
    entry.report = '<img src=x onerror=alert(1)>';
    entry.response.diagnosis.root_cause = '<script>alert(1)</script>';
    saveHistory([entry]);
    const user = userEvent.setup();
    const mounted = render(<App />);
    await revealHistory(user);
    await user.click(within(recent()).getByRole('button', { name: /<img/ }));
    expect(screen.getByText(entry.response.diagnosis.root_cause)).toBeVisible();
    expect(document.querySelector('img,script,iframe')).toBeNull();
    mounted.unmount();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('PRIVATE'); });
    render(<App />);
    expect(screen.getByText(/Browser storage could not be read/)).toBeVisible();
    expect(start()).toBeDisabled();
    expect(fetch).not.toHaveBeenCalled();
  });
});
