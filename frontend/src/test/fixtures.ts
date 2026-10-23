import { demoResponseSchema, stepSchema } from '../api';
import type { DebugDiagnosis, InvestigationStep } from '../api';

// Test-only fixtures; the application never imports or returns these diagnoses.
export function responseFixture(
  status: DebugDiagnosis['status'] = 'INCONCLUSIVE',
  steps: InvestigationStep[] = [],
) {
  return demoResponseSchema.parse({
    diagnosis: {
      status,
      ...(status === 'RESOLVED' ? {
        affected_endpoint: 'POST /orders',
        root_cause: 'The inventory row is absent.',
        suggested_fix: 'Handle a missing inventory row before accessing its quantity.',
      } : {}),
      ...(status === 'RESOLVED' || status === 'NO_BUG_FOUND' ? {
        evidence: [{ source: 'api_execution', finding: 'Observed the Sandbox response.', reference: 'test-reference' }],
      } : { missing_evidence: ['The investigation needs additional evidence.'] }),
    },
    steps,
  });
}

export const observedSteps = [
  stepSchema.parse({ kind: 'tool_attempt', tool_name: 'execute_api_request', method: 'POST', route: '/orders', success: true, status_code: 500, request_id: '6c0df0f0-0a17-47f8-858e-8a92f3762c65' }),
  stepSchema.parse({ kind: 'tool_attempt', tool_name: 'inspect_server_logs', success: false, error_category: 'TOOL_TIMEOUT' }),
  stepSchema.parse({ kind: 'tool_attempt', tool_name: 'inspect_server_logs', success: true, retry: true }),
  stepSchema.parse({ kind: 'rejected_action', tool_name: 'inspect_api_spec', success: false, error_category: 'TOOL_CALL_LIMIT_REACHED' }),
  stepSchema.parse({ kind: 'session_error', success: false, error_category: 'SESSION_TIMEOUT' }),
  stepSchema.parse({ kind: 'tool_attempt' }),
];

export function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}
