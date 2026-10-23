import type { InvestigationStep } from '../api';

const toolLabels = {
  inspect_api_spec: 'Inspect API contract',
  execute_api_request: 'Execute API request',
  inspect_server_logs: 'Inspect server logs',
  inspect_endpoint_implementation: 'Inspect implementation',
};

export function Timeline({ steps }: { steps: InvestigationStep[] }) {
  if (steps.length === 0) {
    return <div className="empty-state compact">
      <span className="empty-mark" aria-hidden="true">∅</span>
      <h3>No tools were used</h3>
      <p>The agent concluded without executing a tool. The returned diagnosis is shown below.</p>
    </div>;
  }

  return <ol className="timeline" aria-label="Recorded investigation steps">
    {steps.map((step, index) => {
      const label = step.kind === 'rejected_action' ? 'Action rejected'
        : step.kind === 'session_error' ? 'Session error'
        : step.tool_name ? toolLabels[step.tool_name] : 'Tool attempt';
      const outcome = step.kind === 'rejected_action' ? 'Not executed'
        : step.kind === 'session_error' ? 'Interrupted'
        : step.success === true ? (step.tool_name === 'execute_api_request' ? 'Probe completed' : 'Completed')
        : step.success === false ? 'Tool failed' : 'Outcome not recorded';
      const tone = step.success === true ? 'positive' : step.success === false ? 'warning' : 'neutral';

      return <li key={index} className="timeline-step">
        <span className={`step-number ${tone}`} aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
        <div className="step-content">
          <div className="step-heading"><h3>{label}</h3><span className={`badge ${tone}`}>{outcome}</span></div>
          {step.tool_name && <code className="tool-name">{step.tool_name}</code>}
          <div className="step-metadata">
            {(step.method || step.route) && <code>{[step.method, step.route].filter(Boolean).join(' ')}</code>}
            {step.status_code !== null && <span className={`badge ${step.status_code >= 400 ? 'warning' : 'neutral'}`}>HTTP {step.status_code}</span>}
            {step.retry && <span className="badge warning">Timeout retry</span>}
            {step.error_category && <code className="error-category">{step.error_category}</code>}
          </div>
          {step.request_id && <p className="request-id">Request <code>{step.request_id}</code></p>}
        </div>
      </li>;
    })}
  </ol>;
}
