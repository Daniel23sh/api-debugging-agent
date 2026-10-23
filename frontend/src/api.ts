import { z } from 'zod';

export const MAX_ISSUE_CHARS = 4000;
export const scenarioIdSchema = z.enum([
  'missing-inventory', 'wrong-status', 'incorrect-total', 'response-schema',
]);

const text = z.string().trim().min(1);
const optionalText = text.nullable().default(null);
const evidenceSchema = z.strictObject({
  source: z.enum(['api_spec', 'api_execution', 'server_logs', 'endpoint_implementation']),
  finding: text,
  reference: optionalText,
});

export const diagnosisSchema = z.strictObject({
  status: z.enum(['RESOLVED', 'NO_BUG_FOUND', 'INCONCLUSIVE', 'LIMIT_REACHED']),
  affected_endpoint: optionalText,
  root_cause: optionalText,
  evidence: z.array(evidenceSchema).default([]),
  missing_evidence: z.array(text).default([]),
  suggested_fix: optionalText,
}).refine((diagnosis) => {
  switch (diagnosis.status) {
    case 'RESOLVED':
      return Boolean(diagnosis.affected_endpoint && diagnosis.root_cause
        && diagnosis.suggested_fix && diagnosis.evidence.length);
    case 'NO_BUG_FOUND':
      return diagnosis.evidence.length > 0 && diagnosis.root_cause === null
        && diagnosis.suggested_fix === null;
    case 'INCONCLUSIVE':
      return diagnosis.missing_evidence.length > 0 && diagnosis.root_cause === null;
    case 'LIMIT_REACHED':
      return diagnosis.evidence.length > 0 || diagnosis.missing_evidence.length > 0;
  }
});

export const stepSchema = z.strictObject({
  kind: z.enum(['tool_attempt', 'rejected_action', 'session_error']),
  tool_name: z.enum([
    'inspect_api_spec', 'execute_api_request', 'inspect_server_logs',
    'inspect_endpoint_implementation',
  ]).nullable().default(null),
  method: z.enum(['GET', 'POST', 'PATCH']).nullable().default(null),
  route: z.enum([
    '/products/{product_id}', '/inventory/{product_id}', '/orders',
    '/orders/{order_id}', '/payments',
  ]).nullable().default(null),
  success: z.boolean().nullable().default(null),
  status_code: z.number().int().min(100).max(599).nullable().default(null),
  request_id: z.string().regex(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  ).nullable().default(null),
  error_category: z.enum([
    'ENDPOINT_NOT_ALLOWED', 'TOOL_TIMEOUT', 'TOOL_HTTP_ERROR', 'TOOL_DATABASE_ERROR',
    'LOG_NOT_FOUND', 'SOURCE_RESOLUTION_ERROR', 'TOOL_NOT_FOUND',
    'TOOL_ARGUMENT_VALIDATION_ERROR', 'TOOL_CALL_LIMIT_REACHED',
    'IDENTICAL_TOOL_CALL_LIMIT_REACHED', 'SESSION_TIMEOUT', 'PROVIDER_ERROR',
    'EXECUTION_ERROR', 'CANCELLED',
  ]).nullable().default(null),
  retry: z.boolean().default(false),
});

export const demoResponseSchema = z.strictObject({
  diagnosis: diagnosisSchema,
  steps: z.array(stepSchema),
});

export type ScenarioId = z.infer<typeof scenarioIdSchema>;
export type DebugDiagnosis = z.infer<typeof diagnosisSchema>;
export type InvestigationStep = z.infer<typeof stepSchema>;
export type DemoResponse = z.infer<typeof demoResponseSchema>;

// Python counts Unicode code points, while HTML maxLength counts UTF-16 units.
export const issueLength = (issue: string) => Array.from(issue.trim()).length;

export async function investigate(issue: string, scenarioId: ScenarioId | null): Promise<DemoResponse> {
  const length = issueLength(issue);
  if (length === 0 || length > MAX_ISSUE_CHARS) {
    throw new Error('Enter a report between 1 and 4,000 characters.');
  }

  let response: Response;
  try {
    response = await fetch('/demo/debug', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ issue: issue.trim(), ...(scenarioId ? { scenario_id: scenarioId } : {}) }),
    });
  } catch {
    throw new Error('Could not reach the Agent Service. Check that Docker Compose and the frontend proxy are running.');
  }

  if (!response.ok) {
    switch (response.status) {
      case 422:
        throw new Error('Report rejected (HTTP 422). Use 1–4,000 characters and a supported scenario.');
      case 503:
        throw new Error('Live investigations require an OpenAI API key on the Agent Service (HTTP 503). Configure it in the backend, then restart the Agent Service.');
      case 502:
        throw new Error('The demo investigation failed on the Agent Service (HTTP 502). No diagnosis was received.');
      default:
        throw new Error(`The Agent Service returned HTTP ${response.status}. No diagnosis was received.`);
    }
  }

  try {
    return demoResponseSchema.parse(await response.json());
  } catch {
    throw new Error('The Agent Service returned an unexpected response. No diagnosis was displayed.');
  }
}
