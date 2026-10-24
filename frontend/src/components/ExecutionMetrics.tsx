import type { ExecutionMetrics as Metrics } from '../api';

export function ExecutionMetrics({ metrics }: { metrics: Metrics | null }) {
  const number = (value: number | null | undefined) => value == null ? 'Unavailable' : value.toLocaleString('en-US');
  return <section id="metrics-panel" tabIndex={-1} className="metrics-panel" aria-labelledby="metrics-title">
    <div className="section-heading"><h2 id="metrics-title">Execution at a glance</h2><span>Measured by this run</span></div>
    <dl className="metrics-grid">
      <div><dt>Agent duration</dt><dd>{metrics?.duration_seconds == null ? 'Unavailable' : `${metrics.duration_seconds.toFixed(2)} s`}</dd></div>
      <div><dt>Tool attempts</dt><dd>{number(metrics?.tool_attempt_count)}</dd></div>
      <div><dt>Model requests</dt><dd>{number(metrics?.model_request_count)}</dd></div>
      <div><dt>Total tokens</dt><dd>{number(metrics?.total_tokens)}</dd></div>
    </dl>
    <p className="metrics-note">Input tokens: {number(metrics?.input_tokens)} · Output tokens: {number(metrics?.output_tokens)}. Each token sum requires usage for every observed model request; unavailable does not mean zero.</p>
    <details className="metric-definitions"><summary>What is measured?</summary><p>Agent duration covers the decision and tool loop, including retries. It excludes fixture preparation, provider construction, and browser/network time. Tool attempts include failed executions and retries; rejected actions are excluded. Model requests count observed provider calls, including corrections and errors, rather than agent decisions. Providers without model hooks report unavailable usage and request counts.</p></details>
  </section>;
}
