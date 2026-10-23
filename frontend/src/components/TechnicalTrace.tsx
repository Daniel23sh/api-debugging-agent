import { phoenixTraceUrl } from '../api';

export function TechnicalTrace({ traceId }: { traceId: string | null }) {
  if (traceId === null) return null;

  return <div className="technical-trace">
    <a href={phoenixTraceUrl(traceId)} target="_blank" rel="noopener noreferrer">
      View technical trace in Phoenix <span aria-hidden="true">↗</span>
    </a>
    <p>Phoenix export may still be pending or unavailable. If the trace isn’t found, reopen this link later; no new investigation is needed.</p>
  </div>;
}
