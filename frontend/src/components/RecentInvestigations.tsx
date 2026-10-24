import { useRef } from 'react';
import type { RecentHistory } from '../history';

export function RecentInvestigations({ history, currentId, loading, onOpen, onClear }: {
  history: RecentHistory; currentId: string | null; loading: boolean;
  onOpen: (id: string) => void; onClear: () => void;
}) {
  const confirmation = useRef<HTMLDialogElement>(null);
  const clearButton = useRef<HTMLButtonElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const deleteButton = useRef<HTMLButtonElement>(null);

  function openConfirmation() {
    confirmation.current?.showModal();
    cancelButton.current?.focus();
  }

  return <section className="recent-history" aria-label="Recent investigations">
    <details>
      <summary id="recent-summary">Recent investigations ({history.entries.length})<span>Browser-local history</span></summary>
      <div className="history-content">
        <p className="history-help">Up to five completed investigations are saved automatically when browser storage is available, without encryption. Opening one makes no new request. Your unfinished draft stays in memory.</p>
        {history.entries.length === 0 ? <p className="history-help">No saved investigations yet.</p> : <ol className="history-list">{history.entries.map((entry) => {
          const preview = Array.from(entry.report).slice(0, 120).join('');
          return <li key={entry.id}><button type="button" disabled={loading} aria-current={currentId === entry.id ? 'true' : undefined} onClick={() => onOpen(entry.id)}>
            <span className="history-report">{preview}{preview.length < entry.report.length ? '…' : ''}</span>
            <span className="history-meta"><code>{entry.response.diagnosis.status}</code><time dateTime={entry.completedAt}>{new Date(entry.completedAt).toLocaleString()}</time>{currentId === entry.id && <span>Viewing</span>}</span>
          </button></li>;
        })}</ol>}
        <button ref={clearButton} type="button" className="secondary-button" disabled={loading || (history.entries.length === 0 && history.notice === null)} onClick={openConfirmation}>Clear history</button>
      </div>
    </details>
    {history.notice && <p className="history-notice" role="status">{history.notice}</p>}
    <dialog ref={confirmation} className="history-clear-dialog" aria-labelledby="clear-history-title" aria-describedby="clear-history-description clear-history-preserved"
      onCancel={(event) => { event.preventDefault(); confirmation.current?.close(); }}
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return;
        if (event.shiftKey && document.activeElement === cancelButton.current) {
          event.preventDefault();
          (deleteButton.current?.disabled ? cancelButton.current : deleteButton.current)?.focus();
        } else if (!event.shiftKey && document.activeElement === deleteButton.current) {
          event.preventDefault();
          cancelButton.current?.focus();
        }
      }}
      onClose={() => { (clearButton.current?.disabled ? document.getElementById('recent-summary') : clearButton.current)?.focus({ preventScroll: true }); }}>
      <span className="confirmation-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5" /></svg></span>
      <p className="eyebrow">BROWSER-LOCAL DATA</p>
      <h2 id="clear-history-title">Clear saved investigations?</h2>
      <p id="clear-history-description">This removes APILens history saved in this browser. You won’t be able to reopen those records after clearing.</p>
      <p id="clear-history-preserved">Your displayed result and unfinished draft will stay open.</p>
      <div className="confirmation-actions">
        <button ref={cancelButton} type="button" className="secondary-button" onClick={() => confirmation.current?.close()}>Cancel</button>
        <button ref={deleteButton} type="button" className="delete-history-button" disabled={loading} onClick={() => { confirmation.current?.close(); onClear(); }}>Delete saved history</button>
      </div>
    </dialog>
  </section>;
}
