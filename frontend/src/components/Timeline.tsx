import type { InvestigationStep } from '../api';
import { CopyButton } from './CopyButton';

const toolLabels = {
  inspect_api_spec: 'Inspect API contract',
  execute_api_request: 'Execute API request',
  inspect_server_logs: 'Inspect server logs',
  inspect_endpoint_implementation: 'Inspect implementation',
};

const descriptions = {
  inspect_api_spec: 'Reads the documented request and response contract for an approved endpoint.',
  execute_api_request: 'Sends an approved Sandbox HTTP request and records its response status and request ID.',
  inspect_server_logs: 'Looks up Sandbox logs using a request ID from an HTTP execution.',
  inspect_endpoint_implementation: 'Reads the approved implementation sections for an endpoint.',
};

export function Timeline({ steps }: { steps: InvestigationStep[] }) {
  if (steps.length === 0) {
    return <div className="empty-state compact">
      <span className="empty-mark" aria-hidden="true">∅</span>
      <h3>No tools were used</h3>
      <p>The agent concluded without executing a tool. Review the outcome and evidence above.</p>
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
          {step.tool_name && step.kind === 'tool_attempt' && <p className="step-description">{descriptions[step.tool_name]}</p>}
          <div className="step-metadata">
            {(step.method || step.route) && <code>{[step.method, step.route].filter(Boolean).join(' ')}</code>}
            {step.status_code !== null && <span className={`badge ${step.status_code >= 400 ? 'warning' : 'neutral'}`}>HTTP {step.status_code}</span>}
            {step.retry && <span className="badge warning">Timeout retry</span>}
          </div>
          <details className="step-details"><summary>Technical details</summary>
            {step.tool_name && <code className="tool-name">{step.tool_name}</code>}
            {step.error_category && <p className="warning"><code>{step.error_category}</code></p>}
            {step.request_id ? <div className="request-id">Request ID <code>{step.request_id}</code><CopyButton label={`Copy request ID for step ${index + 1}`} text={step.request_id} /></div> : <p className="muted">No request ID recorded.</p>}
          </details>
        </div>
      </li>;
    })}
  </ol>;
}
