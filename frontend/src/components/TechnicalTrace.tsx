import { useEffect, useRef, useState } from 'react';
import { phoenixTraceUrl } from '../api';

const desktopQuery = '(min-width: 1280px) and (min-height: 720px)';

export function TechnicalTrace({ traceId }: { traceId: string | null }) {
  const [wide, setWide] = useState(() => window.matchMedia(desktopQuery).matches);
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const external = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    const media = window.matchMedia(desktopQuery);
    const update = () => { setWide(media.matches); if (!media.matches) setOpen(false); };
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    const viewer = dialog.current;
    if (open && wide) { viewer?.showModal(); close.current?.focus(); }
    else if (viewer?.open) viewer.close();
    return () => { if (viewer?.open) viewer.close(); };
  }, [open, wide]);

  if (traceId === null) return null;
  const url = phoenixTraceUrl(traceId);
  return <section id="trace-section" tabIndex={-1} className="technical-trace" aria-labelledby="trace-title">
    <p className="eyebrow">DEEPER OBSERVABILITY</p><h2 id="trace-title">Explore the full investigation</h2>
    <p>The original Phoenix UI shows the technical trace for this run.</p>
    <div className="trace-actions">
      {wide && <button ref={trigger} type="button" className="secondary-button" onClick={() => setOpen(true)}>Explore Full Trace</button>}
      <a ref={external} href={url} target="_blank" rel="noopener noreferrer">Open in Phoenix <span aria-hidden="true">↗</span><span className="sr-only"> (opens in a new tab)</span></a>
    </div>
    {!wide && <p>The embedded viewer needs a desktop viewport at least 1280 × 720. Open Phoenix separately on smaller screens.</p>}
    <p>Phoenix export may still be pending or unavailable. If the trace isn’t found, reopen this link later; no new investigation is needed.</p>
    <dialog ref={dialog} className="trace-dialog" aria-labelledby="viewer-title" aria-describedby="viewer-help"
      onCancel={(event) => { event.preventDefault(); setOpen(false); }}
      onClose={() => { setOpen(false); (wide ? trigger.current : external.current)?.focus(); }}>
      <div className="viewer-header"><div><h2 id="viewer-title">Technical trace</h2><p id="viewer-help">Ingestion may be delayed. If the viewer is blank or unavailable, use the external link or try opening it later. Escape closes this dialog from its toolbar; Phoenix handles keyboard shortcuts inside the frame.</p></div>
        <a href={url} target="_blank" rel="noopener noreferrer">Open in Phoenix ↗</a>
        <button ref={close} type="button" className="secondary-button" onClick={() => setOpen(false)}>Close trace</button>
      </div>
      {open && wide && <iframe title="Phoenix investigation trace" src={url} />}
    </dialog>
  </section>;
}
