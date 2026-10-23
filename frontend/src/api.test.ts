import { describe, expect, it, vi } from 'vitest';
import { demoResponseSchema, investigate, issueLength, phoenixTraceUrl, stepSchema } from './api';
import { jsonResponse, responseFixture } from './test/fixtures';

describe('Demo API client', () => {
  it('sends the selected scenario through a relative request', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture()));
    await investigate('  report  ', 'missing-inventory');
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledWith('/demo/debug', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ issue: 'report', scenario_id: 'missing-inventory' }),
    });
  });

  it('omits scenario_id for free-form requests', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture()));
    await investigate('custom report', null);
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)).toEqual({ issue: 'custom report' });
  });

  it.each(['', ' \n\t ', 'x'.repeat(4001)])('rejects invalid input without a request', async (issue) => {
    await expect(investigate(issue, null)).rejects.toThrow('1 and 4,000');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('counts trimmed Unicode code points like the backend', async () => {
    expect(issueLength(`  ${'😀'.repeat(4000)} \n`)).toBe(4000);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(responseFixture()));
    await expect(investigate('😀'.repeat(4000), null)).resolves.toBeDefined();
  });

  it.each([422, 503, 502, 404, 500])('handles HTTP %s without exposing server diagnostics or retrying', async (status) => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ detail: 'PRIVATE-provider-error-api-key' }, status));
    await expect(investigate('report', null)).rejects.toThrow(`HTTP ${status}`);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('handles a network failure without retrying', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error('PRIVATE-connection-detail'));
    await expect(investigate('report', null)).rejects.toThrow('Could not reach the Agent Service');
    expect(fetch).toHaveBeenCalledOnce();
  });

  it.each([
    null, [], {}, { diagnosis: { status: 'RESOLVED' }, steps: [] },
    { diagnosis: { status: 'NO_BUG_FOUND', evidence: [] }, steps: [] },
    { diagnosis: { status: 'INCONCLUSIVE' }, steps: [] },
    { diagnosis: { status: 'LIMIT_REACHED' }, steps: [] },
    { ...responseFixture(), steps: [{ kind: 'invented' }] },
    { ...responseFixture(), steps: [{ kind: 'tool_attempt', tool_name: 'PRIVATE' }] },
    { ...responseFixture(), steps: [{ kind: 'tool_attempt', route: '/products/PRIVATE' }] },
    { ...responseFixture(), steps: [{ kind: 'tool_attempt', request_id: 'PRIVATE' }] },
    { ...responseFixture(), steps: [{ kind: 'tool_attempt', status_code: 600 }] },
    { ...responseFixture(), steps: [{ kind: 'tool_attempt', status_code: true }] },
    { ...responseFixture(), steps: [{ kind: 'tool_attempt', reasoning: 'PRIVATE' }] },
  ])('rejects malformed or contract-invalid response %#', async (body) => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(body));
    await expect(investigate('report', null)).rejects.toThrow('unexpected response');
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('rejects HTML masquerading as a successful response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('<html>proxy fallback</html>'));
    await expect(investigate('report', null)).rejects.toThrow('unexpected response');
  });

  it('accepts absent optional fields using the backend defaults', () => {
    const response = demoResponseSchema.parse({
      diagnosis: { status: 'INCONCLUSIVE', missing_evidence: ['More context'] },
      steps: [{ kind: 'tool_attempt' }],
    });
    expect(response.steps).toEqual([stepSchema.parse({ kind: 'tool_attempt' })]);
    expect(response.diagnosis.root_cause).toBeNull();
    expect(response.diagnosis.evidence).toEqual([]);
    expect(response.trace_id).toBeNull();
  });

  it('builds an exact trace URL only on the trusted local Phoenix origin', async () => {
    const traceId = '0123456789abcdef0123456789abcdef';
    const response = { ...responseFixture(), trace_id: traceId };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(response));
    expect(await investigate('report', null)).toEqual(response);
    expect(phoenixTraceUrl(traceId)).toBe(`http://127.0.0.1:6006/redirects/traces/${traceId}`);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it.each([
    '0'.repeat(32), 'A'.repeat(32), 'a'.repeat(31), 'a'.repeat(33), `${'a'.repeat(32)}\n`,
    'https://PRIVATE.example/trace', '../PRIVATE', 'javascript:alert(1)', 123,
  ])('rejects malformed trace metadata without displaying it or navigating %#', async (traceId) => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ ...responseFixture(), trace_id: traceId }));
    await expect(investigate('report', null)).rejects.toThrow('unexpected response');
    expect(() => phoenixTraceUrl(traceId as string)).toThrow();
    expect(fetch).toHaveBeenCalledOnce();
  });
});
