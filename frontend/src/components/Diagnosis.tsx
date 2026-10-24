import type { DebugDiagnosis } from '../api';
import { CopyButton } from './CopyButton';

const statusLabels = {
  RESOLVED: { title: 'Root cause identified', tone: 'positive' },
  NO_BUG_FOUND: { title: 'No bug found', tone: 'neutral' },
  INCONCLUSIVE: { title: 'More evidence needed', tone: 'warning' },
  LIMIT_REACHED: { title: 'Investigation limit reached', tone: 'warning' },
};

const sourceLabels = {
  api_spec: 'API contract', api_execution: 'API execution',
  server_logs: 'Server logs', endpoint_implementation: 'Implementation',
};

export function diagnosisText(diagnosis: DebugDiagnosis): string {
  return [
    `Status: ${diagnosis.status}`,
    diagnosis.affected_endpoint && `Endpoint: ${diagnosis.affected_endpoint}`,
    diagnosis.root_cause && `Root cause: ${diagnosis.root_cause}`,
    diagnosis.evidence.length > 0 && `Evidence:\n${diagnosis.evidence.map((item) => `- [${item.source}] ${item.finding}${item.reference ? ` (Reference: ${item.reference})` : ''}`).join('\n')}`,
    diagnosis.missing_evidence.length > 0 && `Missing evidence:\n${diagnosis.missing_evidence.map((item) => `- ${item}`).join('\n')}`,
    diagnosis.suggested_fix && `Suggested fix: ${diagnosis.suggested_fix}`,
  ].filter(Boolean).join('\n\n');
}

export function Outcome({ diagnosis }: { diagnosis: DebugDiagnosis }) {
  const status = statusLabels[diagnosis.status];
  return <div className="diagnosis-content outcome-content">
    <div className={`diagnosis-status ${status.tone}`}>
      <span className="status-dot" aria-hidden="true" />
      <div><h3>{status.title}</h3><code>{diagnosis.status}</code></div>
    </div>
    {diagnosis.affected_endpoint && <div className="finding-block"><h3>Affected endpoint</h3><code className="endpoint">{diagnosis.affected_endpoint}</code></div>}
    {diagnosis.root_cause && <div className="finding-block"><h3>Root cause</h3><p>{diagnosis.root_cause}</p></div>}
    <CopyButton label="Copy Diagnosis" text={diagnosisText(diagnosis)} />
  </div>;
}

export function Diagnosis({ diagnosis }: { diagnosis: DebugDiagnosis }) {
  return <div className="diagnosis-content">
    {diagnosis.evidence.length > 0 && <div className="finding-block">
      <h3>Evidence <span className="count">{diagnosis.evidence.length}</span></h3>
      <ul className="evidence-list">{diagnosis.evidence.map((item, index) => <li key={index}>
        <span className="source-label">{sourceLabels[item.source]}</span>
        <p>{item.finding}</p>
        {item.reference && <p className="reference">Reference <code>{item.reference}</code></p>}
      </li>)}</ul>
    </div>}
    {diagnosis.missing_evidence.length > 0 && <div className="finding-block missing-evidence">
      <h3>Missing evidence</h3>
      <ul>{diagnosis.missing_evidence.map((item, index) => <li key={index}>{item}</li>)}</ul>
    </div>}
    {diagnosis.suggested_fix && <div className="finding-block suggested-fix"><h3>Suggested fix</h3><p>{diagnosis.suggested_fix}</p></div>}
    <p className="diagnosis-footnote">An evidence-based diagnosis, not an applied fix. Review the findings before making changes.</p>
  </div>;
}
