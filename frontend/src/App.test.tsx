import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import App from './App';
import { scenarios } from './scenarios';
import { jsonResponse, observedSteps, responseFixture } from './test/fixtures';

const reportInput = () => screen.getByRole('textbox', { name: /Bug report/ });
const action = () => screen.getByRole('button', { name: /Start investigation/ });
const costCheckbox = () => screen.getByRole('checkbox', { name: /may incur API costs/ });

async function prepare(issue = 'Investigate POST /orders') {
  const user = userEvent.setup();
  render(<App />);
  await user.type(reportInput(), issue);
  await user.click(costCheckbox());
  return user;
}

describe('APILens workspace', () => {
  it('renders accessible empty states and never requests the API on page load', () => {
    render(<App />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('From bug report');
    expect(reportInput()).toBeRequired();
    expect(action()).toBeDisabled();
    expect(costCheckbox()).not.toBeChecked();
    expect(screen.getByText('Every step has a source')).toBeVisible();
    expect(screen.queryByLabelText('Recorded investigation steps')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Open in Phoenix/ })).not.toBeInTheDocument();
    expect(document.querySelector('iframe')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(scenarios)('selects $id and prefills its report without a request', async (scenario) => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: new RegExp(scenario.title) }));
    expect(reportInput()).toHaveValue(scenario.issue);
    expect(screen.getByRole('button', { name: new RegExp(scenario.title) })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/fixtures selected/)).toHaveTextContent(scenario.title);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('replaces a selected report when another scenario is selected', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: /Missing inventory/ }));
    await user.click(screen.getByRole('button', { name: /Response schema/ }));
    expect(reportInput()).toHaveValue(scenarios[3].issue);
    expect(screen.getByRole('button', { name: /Missing inventory/ })).toHaveAttribute('aria-pressed', 'false');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('clears the scenario after manual editing and submits free-form fixtures', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: /Response schema/ }));
    await user.type(reportInput(), ' Edited.');
    expect(screen.getByText('Free-form')).toBeVisible();
    expect(screen.getByText(/No scenario selected/)).toBeVisible();
    expect(screen.getByRole('button', { name: /Response schema/ })).toHaveAttribute('aria-pressed', 'false');
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture()));
    await user.click(costCheckbox());
    await user.click(action());
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)).toEqual({ issue: `${scenarios[3].issue} Edited.` });
    expect(await screen.findByText('No tools were used')).toBeVisible();
  });

  it.each(scenarios)('sends the exact scenario_id for $id', async (scenario) => {
    const user = userEvent.setup();
    render(<App />);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture()));
    await user.click(screen.getByRole('button', { name: new RegExp(scenario.title) }));
    await user.click(costCheckbox());
    await user.click(action());
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)).toEqual({ issue: scenario.issue, scenario_id: scenario.id });
  });

  it('requires both a nonempty report and cost acknowledgment', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(reportInput(), '  \n  ');
    await user.click(costCheckbox());
    expect(action()).toBeDisabled();
    await user.type(reportInput(), 'A report');
    expect(action()).toBeEnabled();
    await user.click(costCheckbox());
    expect(action()).toBeDisabled();
    fireEvent.submit(reportInput().closest('form')!);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('enforces the trimmed Unicode-aware character limit without silently truncating', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(costCheckbox());
    fireEvent.change(reportInput(), { target: { value: ` \n${'😀'.repeat(4000)}\t ` } });
    expect(screen.getByText('4,000 / 4,000')).toBeVisible();
    expect(action()).toBeEnabled();
    fireEvent.change(reportInput(), { target: { value: '😀'.repeat(4001) } });
    expect(screen.getByText('4,001 / 4,000')).toBeVisible();
    expect(reportInput()).toHaveAttribute('aria-invalid', 'true');
    expect(reportInput()).toHaveValue('😀'.repeat(4001));
    expect(action()).toBeDisabled();
    fireEvent.submit(reportInput().closest('form')!);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('locks the submitted report and prevents duplicate requests while loading', async () => {
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const user = await prepare();
    await user.click(action());
    expect(screen.getByRole('button', { name: /Investigating/ })).toBeDisabled();
    expect(reportInput()).toBeDisabled();
    expect(reportInput()).toHaveValue('Investigate POST /orders');
    expect(costCheckbox()).toBeDisabled();
    expect(screen.getByRole('button', { name: /Missing inventory/ })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Investigation in progress');
    expect(screen.getByRole('heading', { name: 'Investigation in progress' })).toBeVisible();
    expect(screen.queryByLabelText('Recorded investigation steps')).not.toBeInTheDocument();
    fireEvent.submit(reportInput().closest('form')!);
    fireEvent.submit(reportInput().closest('form')!);
    expect(fetch).toHaveBeenCalledOnce();
    await act(async () => { finish(jsonResponse(responseFixture())); });
    expect(await screen.findByText('No tools were used')).toBeVisible();
    expect(reportInput()).toBeEnabled();
  });

  it('preserves returned order, actual failures, retries, rejection, and absent metadata', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture('LIMIT_REACHED', observedSteps)));
    const user = await prepare();
    await user.click(action());
    await user.click(await screen.findByRole('link', { name: /Timeline \(6\)/ }));
    const timeline = await screen.findByRole('list', { name: 'Recorded investigation steps' });
    const steps = within(timeline).getAllByRole('listitem');
    expect(steps).toHaveLength(6);
    expect(steps[0]).toHaveTextContent('Execute API request');
    expect(steps[0]).toHaveTextContent('Probe completed');
    expect(steps[0]).toHaveTextContent('HTTP 500');
    expect(steps[0]).not.toHaveTextContent('Tool failed');
    expect(steps[0]).toHaveTextContent(observedSteps[0].request_id!);
    expect(steps[1]).toHaveTextContent('Tool failed');
    expect(steps[2]).toHaveTextContent('Timeout retry');
    expect(steps[3]).toHaveTextContent('Action rejected');
    expect(steps[3]).toHaveTextContent('Not executed');
    expect(steps[4]).toHaveTextContent('Session error');
    expect(steps[5]).toHaveTextContent('Outcome not recorded');
  });

  it.each(['RESOLVED', 'NO_BUG_FOUND', 'INCONCLUSIVE', 'LIMIT_REACHED'] as const)('renders the authoritative %s diagnosis', async (status) => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture(status)));
    const user = await prepare();
    await user.click(action());
    expect(await screen.findByText(status, { selector: '.diagnosis-status code' })).toBeVisible();
    expect(screen.getByText('No tools were used')).toBeVisible();
    if (status === 'RESOLVED') {
      expect(screen.getByText('The inventory row is absent.')).toBeVisible();
      expect(screen.getByRole('heading', { name: 'Suggested fix' })).toBeVisible();
      await user.click(screen.getByRole('link', { name: /Evidence \(1\)/ }));
      expect(screen.getByText('API execution', { selector: '.source-label' })).toBeVisible();
      expect(screen.getByText('test-reference')).toBeVisible();
    }
  });

  it('clears a previous result when the report changes or a new run starts', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture('RESOLVED', observedSteps)));
    const user = await prepare();
    await user.click(action());
    expect(await screen.findByText('RESOLVED', { selector: '.diagnosis-status code' })).toBeVisible();
    await user.click(screen.getByRole('link', { name: /Edit report \/ run again/ }));
    await user.click(screen.getByRole('button', { name: /Wrong status/ }));
    expect(screen.queryByText('RESOLVED', { selector: '.diagnosis-status code' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Recorded investigation steps')).not.toBeInTheDocument();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({}, 503));
    await user.click(action());
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 503');
    expect(screen.queryByText('The inventory row is absent.')).not.toBeInTheDocument();
  });

  it.each([422, 503, 502, 500])('renders an actionable HTTP %s error without server details', async (status) => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ detail: 'PRIVATE-secret' }, status));
    const user = await prepare();
    await user.click(action());
    const error = await screen.findByRole('alert');
    expect(error).toHaveTextContent(`HTTP ${status}`);
    expect(error).toHaveTextContent('No automatic retries');
    expect(error).not.toHaveTextContent('PRIVATE-secret');
    expect(action()).toBeEnabled();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('renders a network error and allows explicit recovery', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('PRIVATE-network-error'));
    const user = await prepare();
    await user.click(action());
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the Agent Service');
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture()));
    await user.click(action());
    expect(await screen.findByText('INCONCLUSIVE', { selector: '.diagnosis-status code' })).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('offers safe exact-trace navigation after completion without loading Phoenix', async () => {
    const traceId = '0123456789abcdef0123456789abcdef';
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ ...responseFixture(), trace_id: traceId }));
    const user = await prepare();
    await user.click(action());
    const link = await screen.findByRole('link', { name: /Open in Phoenix/ });
    expect(link).toHaveAttribute('href', `http://127.0.0.1:6006/redirects/traces/${traceId}`);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByText(/export may still be pending or unavailable/)).toBeVisible();
    expect(document.querySelector('iframe')).toBeNull();
    expect(fetch).toHaveBeenCalledOnce();
    expect(screen.getByRole('region', { name: 'Investigation outcome' })).toHaveFocus();
  });

  it('hides trace navigation when tracing is absent', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture()));
    const user = await prepare();
    await user.click(action());
    expect(await screen.findByText('INCONCLUSIVE', { selector: '.diagnosis-status code' })).toBeVisible();
    expect(screen.queryByRole('link', { name: /Open in Phoenix/ })).not.toBeInTheDocument();
  });

  it('clears a previous trace on editing, loading, and failure', async () => {
    const response = { ...responseFixture(), trace_id: '0123456789abcdef0123456789abcdef' };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(response));
    const user = await prepare();
    await user.click(action());
    await screen.findByRole('link', { name: /Open in Phoenix/ });
    await user.click(screen.getByRole('link', { name: /Edit report \/ run again/ }));
    await user.type(reportInput(), ' Edited.');
    expect(screen.queryByRole('link', { name: /Open in Phoenix/ })).not.toBeInTheDocument();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(response));
    await user.click(action());
    await screen.findByRole('link', { name: /Open in Phoenix/ });

    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await user.click(screen.getByRole('link', { name: /Edit report \/ run again/ }));
    await user.click(action());
    expect(screen.queryByRole('link', { name: /Open in Phoenix/ })).not.toBeInTheDocument();
    await act(async () => { finish(jsonResponse({ ...response, detail: 'PRIVATE' }, 502)); });
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 502');
    expect(screen.queryByRole('link', { name: /Open in Phoenix/ })).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('rejects an unexpected response instead of displaying a partial diagnosis', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ diagnosis: { status: 'RESOLVED' }, steps: [] }));
    const user = await prepare();
    await user.click(action());
    expect(await screen.findByRole('alert')).toHaveTextContent('unexpected response');
    expect(screen.queryByText('RESOLVED', { selector: '.diagnosis-status code' })).not.toBeInTheDocument();
  });

  it('renders every model-generated field as text without creating HTML elements', async () => {
    const html = '<img src=x onerror="window.APILENS_XSS=1"><script>alert(1)</script>';
    const response = responseFixture('RESOLVED');
    response.diagnosis.affected_endpoint = html;
    response.diagnosis.root_cause = html;
    response.diagnosis.evidence[0].finding = html;
    response.diagnosis.evidence[0].reference = 'javascript:alert(1)';
    response.diagnosis.missing_evidence = [html];
    response.diagnosis.suggested_fix = html;
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(response));
    const user = await prepare();
    await user.click(action());
    await screen.findByText('RESOLVED', { selector: '.diagnosis-status code' });
    await user.click(screen.getByRole('link', { name: /Evidence \(1\)/ }));
    expect(screen.getAllByText(html)).toHaveLength(5);
    expect(document.querySelector('img, script, iframe')).toBeNull();
    expect(screen.getByText('javascript:alert(1)')).not.toHaveAttribute('href');
  });

  it('supports keyboard scenario selection and cost acknowledgment', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.tab(); // Skip link.
    await user.tab(); // Brand.
    await user.tab(); // Recent investigations disclosure.
    await user.tab();
    expect(screen.getByRole('button', { name: /Missing inventory/ })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(reportInput()).toHaveValue(scenarios[0].issue);
    costCheckbox().focus();
    await user.keyboard(' ');
    expect(costCheckbox()).toBeChecked();
    expect(action()).toBeEnabled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('orders outcome, measured metrics, evidence, timeline, and Phoenix without repeating the cause', async () => {
    const response = { ...responseFixture('RESOLVED', observedSteps), trace_id: '0123456789abcdef0123456789abcdef', metrics: {
      duration_seconds: 2.5, tool_attempt_count: 4, model_request_count: 2,
      input_tokens: 10, output_tokens: 5, total_tokens: 15,
    } };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(response));
    const user = await prepare();
    await user.click(action());
    await screen.findByRole('heading', { name: 'Root cause identified' });
    const headings = screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent);
    expect(headings).toEqual(['What’s not working?', 'Investigation outcome', 'Execution at a glance', 'Diagnosis & evidence', 'Follow the evidence', 'Explore the full investigation']);
    expect(screen.getAllByText('The inventory row is absent.')).toHaveLength(1);
    expect(screen.getByRole('region', { name: 'Investigation outcome' })).toHaveFocus();
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({ block: 'start', behavior: 'auto' });
  });

  it('focuses and scrolls to the completed outcome on mobile', async () => {
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query.includes('max-width'), addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture('NO_BUG_FOUND')));
    const user = await prepare();
    await user.click(action());
    expect(await screen.findByRole('heading', { name: 'No bug found' })).toBeVisible();
    expect(screen.getByRole('region', { name: 'Investigation outcome' })).toHaveFocus();
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledExactlyOnceWith({ block: 'start', behavior: 'auto' });
    expect(screen.getByText('NO_BUG_FOUND', { selector: '.diagnosis-status code' }).closest('.diagnosis-status')).toHaveClass('neutral');
  });

  it('reveals long evidence and chronological activity through keyboard-operable section links', async () => {
    const response = responseFixture('RESOLVED', observedSteps);
    response.diagnosis.root_cause = 'The complete root cause. '.repeat(30);
    response.diagnosis.evidence = Array.from({ length: 8 }, (_, index) => ({ source: 'api_execution' as const, finding: `Recorded finding ${index + 1}. ${'Full supporting text. '.repeat(20)}`, reference: null }));
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(response));
    const user = await prepare();
    await user.click(action());
    const navigation = await screen.findByRole('navigation', { name: 'Investigation sections' });
    expect(reportInput()).not.toBeVisible();
    expect(screen.getByText('Handle a missing inventory row before accessing its quantity.')).toBeVisible();
    expect(screen.getByText(response.diagnosis.evidence[0].finding.trim())).not.toBeVisible();
    expect(screen.getByText(response.diagnosis.root_cause.trim())).toBeVisible();
    const evidenceLink = within(navigation).getByRole('link', { name: 'Evidence (8)' });
    evidenceLink.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByText(response.diagnosis.evidence[7].finding.trim())).toBeVisible();
    expect(document.getElementById('evidence-summary')).toHaveFocus();
    await user.click(document.getElementById('evidence-summary')!);
    expect(screen.getByText(response.diagnosis.evidence[7].finding.trim())).not.toBeVisible();
    await user.click(within(navigation).getByRole('link', { name: 'Timeline (6)' }));
    expect(document.getElementById('timeline-summary')).toHaveFocus();
    expect(screen.getByRole('list', { name: 'Recorded investigation steps' })).toBeVisible();
    expect(within(screen.getByRole('list', { name: 'Recorded investigation steps' })).getAllByRole('listitem')).toHaveLength(6);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('returns to the report without a request, preserves consent, and resets disclosures on another run', async () => {
    const response = { ...responseFixture('RESOLVED', observedSteps), trace_id: '0123456789abcdef0123456789abcdef' };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(response));
    const user = await prepare();
    await user.click(action());
    await user.click(await screen.findByRole('link', { name: 'Evidence (1)' }));
    await user.click(screen.getByRole('link', { name: 'Timeline (6)' }));
    await user.click(screen.getByRole('link', { name: /Edit report \/ run again/ }));
    expect(reportInput()).toBeVisible();
    expect(reportInput()).toHaveFocus();
    expect(reportInput()).toHaveValue('Investigate POST /orders');
    expect(costCheckbox()).toBeChecked();
    expect(screen.queryByRole('navigation', { name: 'Investigation sections' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Open in Phoenix/ })).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledOnce();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(response));
    await user.click(action());
    await screen.findByRole('navigation', { name: 'Investigation sections' });
    expect(document.querySelector('.evidence-disclosure')).not.toHaveAttribute('open');
    expect(document.querySelector('.timeline-panel')).not.toHaveAttribute('open');
    expect(screen.getByRole('link', { name: /Back to investigation sections/ })).toHaveAttribute('href', '#result-navigation');
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('keeps uncertainty visible and omits navigation to absent evidence and tracing', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture('LIMIT_REACHED')));
    const user = await prepare();
    await user.click(action());
    const navigation = await screen.findByRole('navigation', { name: 'Investigation sections' });
    expect(within(navigation).getByRole('link', { name: 'Findings' })).toHaveAttribute('href', '#findings-panel');
    expect(within(navigation).queryByRole('link', { name: 'Phoenix' })).not.toBeInTheDocument();
    expect(screen.getByText('The investigation needs additional evidence.')).toBeVisible();
    expect(screen.getByText('No tools were used')).toBeVisible();
  });

  it.each([false, true])('scrolls section links and the return link to stable targets (reduced motion: %s)', async (reducedMotion) => {
    const preferences = window.matchMedia('(prefers-reduced-motion: reduce)');
    vi.mocked(window.matchMedia).mockImplementation((query) => ({ ...preferences, media: query, matches: reducedMotion && query === '(prefers-reduced-motion: reduce)' }));
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture('RESOLVED', observedSteps)));
    const user = await prepare();
    await user.click(action());
    const timelineLink = await screen.findByRole('link', { name: 'Timeline (6)' });
    const scroll = vi.mocked(HTMLElement.prototype.scrollIntoView);
    scroll.mockClear();
    await user.click(timelineLink);
    expect(document.querySelector('.timeline-panel')).toHaveAttribute('open');
    expect(document.getElementById('timeline-summary')).toHaveFocus();
    expect(scroll).toHaveBeenLastCalledWith({ block: 'start', behavior: reducedMotion ? 'auto' : 'smooth' });
    expect(scroll.mock.contexts.at(-1)).toBe(document.getElementById('timeline-summary'));

    await user.click(screen.getByRole('link', { name: /Back to investigation sections/ }));
    const start = document.getElementById('result-navigation');
    expect(start).toHaveClass('results-column');
    expect(start).not.toBe(screen.getByRole('navigation', { name: 'Investigation sections' }));
    expect(start).toHaveFocus();
    expect(scroll.mock.contexts.at(-1)).toBe(start);
    expect(scroll).toHaveBeenLastCalledWith({ block: 'start', behavior: reducedMotion ? 'auto' : 'smooth' });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('uses one overview destination for the outcome and metrics without removing either card', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture('RESOLVED', observedSteps)));
    const user = await prepare();
    await user.click(action());
    const navigation = await screen.findByRole('navigation', { name: 'Investigation sections' });
    expect(within(navigation).queryByRole('link', { name: 'Outcome' })).not.toBeInTheDocument();
    expect(within(navigation).queryByRole('link', { name: 'Metrics' })).not.toBeInTheDocument();
    await user.click(within(navigation).getByRole('link', { name: 'Overview' }));
    const overview = document.getElementById('overview-panel')!;
    expect(overview).toHaveFocus();
    expect(within(overview).getByRole('region', { name: 'Investigation outcome' })).toBeVisible();
    expect(within(overview).getByRole('region', { name: 'Execution at a glance' })).toBeVisible();
    expect(vi.mocked(HTMLElement.prototype.scrollIntoView).mock.contexts.at(-1)).toBe(overview);
    expect(fetch).toHaveBeenCalledOnce();
  });
});
