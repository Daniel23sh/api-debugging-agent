import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { diagnosisText, Diagnosis } from './Diagnosis';
import { CopyButton } from './CopyButton';
import { ExecutionMetrics } from './ExecutionMetrics';
import { TechnicalTrace } from './TechnicalTrace';
import { Timeline } from './Timeline';
import { responseFixture, observedSteps } from '../test/fixtures';

const traceId = '0123456789abcdef0123456789abcdef';
const url = `http://127.0.0.1:6006/redirects/traces/${traceId}`;
function desktop() {
  const events = new EventTarget();
  const media = { matches: true, addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events) };
  vi.stubGlobal('matchMedia', vi.fn(() => media));
  return { media, events };
}

describe('Execution summary', () => {
  it('distinguishes observed zero from unavailable usage and requests', () => {
    render(<ExecutionMetrics metrics={{ duration_seconds: 1.234, tool_attempt_count: 0, model_request_count: null, input_tokens: 0, output_tokens: null, total_tokens: null }} />);
    expect(screen.getByText('1.23 s')).toBeVisible();
    expect(screen.getByText('0', { selector: 'dd' })).toBeVisible();
    expect(screen.getAllByText('Unavailable', { selector: 'dd' })).toHaveLength(2);
    expect(screen.getByText(/Input tokens: 0 · Output tokens: Unavailable/)).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('does not invent metrics when an older response has no measurements', () => {
    render(<ExecutionMetrics metrics={null} />);
    expect(screen.getAllByText('Unavailable', { selector: 'dd' })).toHaveLength(4);
  });
});

describe('Copying actual text', () => {
  it('formats every present diagnosis field and preserves uncertainty', () => {
    const diagnosis = responseFixture('RESOLVED').diagnosis;
    diagnosis.missing_evidence = ['A follow-up check is still needed.'];
    expect(diagnosisText(diagnosis)).toBe([
      'Status: RESOLVED', 'Endpoint: POST /orders', 'Root cause: The inventory row is absent.',
      'Evidence:\n- [api_execution] Observed the Sandbox response. (Reference: test-reference)',
      'Missing evidence:\n- A follow-up check is still needed.',
      'Suggested fix: Handle a missing inventory row before accessing its quantity.',
    ].join('\n\n'));
    expect(diagnosisText(responseFixture().diagnosis)).toBe('Status: INCONCLUSIVE\n\nMissing evidence:\n- The investigation needs additional evidence.');
  });

  it('copies only after an explicit action with accessible confirmation', async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    const text = diagnosisText(responseFixture('LIMIT_REACHED').diagnosis);
    render(<CopyButton text={text} label="Copy Diagnosis" />);
    expect(write).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Copy Diagnosis' }));
    expect(write).toHaveBeenCalledExactlyOnceWith(text);
    expect(screen.getByRole('status')).toHaveTextContent('Copied to clipboard.');
  });

  it('handles clipboard denial without exposing private exception details', async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('PRIVATE'));
    render(<CopyButton text="actual diagnosis" label="Copy Diagnosis" />);
    await user.click(screen.getByRole('button', { name: 'Copy Diagnosis' }));
    expect(screen.getByRole('status')).toHaveTextContent('Copy failed.');
    expect(screen.getByRole('status')).not.toHaveTextContent('PRIVATE');
  });

  it('copies an observed canonical request ID from progressively disclosed metadata', async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    render(<Timeline steps={observedSteps} />);
    const first = within(screen.getByRole('list', { name: 'Recorded investigation steps' })).getAllByRole('listitem')[0];
    expect(within(first).getByText(/Sends an approved Sandbox HTTP request/)).toBeVisible();
    expect(within(first).getByText(observedSteps[0].request_id!)).not.toBeVisible();
    await user.click(within(first).getByText('Technical details'));
    await user.click(within(first).getByRole('button', { name: 'Copy request ID for step 1' }));
    expect(write).toHaveBeenCalledExactlyOnceWith(observedSteps[0].request_id);
    expect(within(first).getByText('HTTP 500')).toBeVisible();
    expect(within(first).getByText('Probe completed')).toBeVisible();
  });

  it('renders exact and similar evidence references as text without speculative links', async () => {
    const user = userEvent.setup();
    const diagnosis = responseFixture('RESOLVED').diagnosis;
    diagnosis.evidence = [
      { source: 'api_execution', finding: 'Observed status', reference: observedSteps[0].request_id },
      { source: 'server_logs', finding: 'Follow-up evidence', reference: `request ${observedSteps[0].request_id}` },
    ];
    render(<Diagnosis diagnosis={diagnosis} />);
    await user.click(screen.getByText('Expand recorded findings and references'));
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText(observedSteps[0].request_id!)).toBeVisible();
  });
});

describe('Native Phoenix viewer', () => {
  it('loads only on explicit desktop action, closes and restores focus', async () => {
    desktop();
    const user = userEvent.setup();
    render(<TechnicalTrace traceId={traceId} />);
    const trigger = screen.getByRole('button', { name: 'Explore Full Trace' });
    expect(document.querySelector('iframe')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
    await user.click(trigger);
    const dialog = screen.getByRole('dialog', { name: 'Technical trace' });
    expect(dialog).toHaveAttribute('open');
    expect(within(dialog).getByText(/Phoenix handles keyboard shortcuts inside the frame/)).toBeVisible();
    expect(screen.getByTitle('Phoenix investigation trace')).toHaveAttribute('src', url);
    expect(within(dialog).getByRole('button', { name: 'Close trace' })).toHaveFocus();
    const fallback = within(dialog).getByRole('link', { name: /Open in Phoenix/ });
    expect(fallback).toHaveAttribute('href', url);
    expect(fallback).toHaveAttribute('rel', 'noopener noreferrer');
    expect(fallback).toHaveAttribute('target', '_blank');
    await user.click(within(dialog).getByRole('button', { name: 'Close trace' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.querySelector('iframe')).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it('handles the native Escape cancel event', async () => {
    desktop();
    const user = userEvent.setup();
    render(<TechnicalTrace traceId={traceId} />);
    const trigger = screen.getByRole('button', { name: 'Explore Full Trace' });
    trigger.focus();
    await user.keyboard('{Enter}');
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('unloads the viewer when resized below the usable desktop viewport', async () => {
    const { media, events } = desktop();
    const user = userEvent.setup();
    render(<TechnicalTrace traceId={traceId} />);
    await user.click(screen.getByRole('button', { name: 'Explore Full Trace' }));
    act(() => { media.matches = false; events.dispatchEvent(new Event('change')); });
    expect(document.querySelector('iframe')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Explore Full Trace' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open in Phoenix/ })).toHaveFocus();
  });

  it('offers only external exact-trace navigation on narrow screens', () => {
    render(<TechnicalTrace traceId={traceId} />);
    expect(screen.queryByRole('button', { name: 'Explore Full Trace' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open in Phoenix/ })).toHaveAttribute('href', url);
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('renders neither viewer nor link when tracing is absent', () => {
    desktop();
    render(<TechnicalTrace traceId={null} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
