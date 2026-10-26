import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { investigate, issueLength, MAX_ISSUE_CHARS } from './api';
import type { DemoResponse, ScenarioId } from './api';
import { Diagnosis, Outcome } from './components/Diagnosis';
import { ExecutionMetrics } from './components/ExecutionMetrics';
import { Timeline } from './components/Timeline';
import { TechnicalTrace } from './components/TechnicalTrace';
import { scenarios } from './scenarios';
import { ResultNavigation, scrollToSection } from './components/ResultNavigation';
import { clearHistory, HISTORY_LIMIT, loadHistory, saveHistory } from './history';
import type { SavedInvestigation } from './history';
import { RecentInvestigations } from './components/RecentInvestigations';

type RunState = { status: 'idle' }
  | { status: 'loading'; report: string }
  | ({ status: 'success'; restored: boolean } & SavedInvestigation)
  | { status: 'error'; report: string; message: string };

export default function App() {
  const [issue, setIssue] = useState('');
  const [scenarioId, setScenarioId] = useState<ScenarioId | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [run, setRun] = useState<RunState>({ status: 'idle' });
  const [history, setHistory] = useState(loadHistory);
  const inFlight = useRef(false);
  const returningToDraft = useRef(false);
  const outcome = useRef<HTMLElement>(null);
  const loading = run.status === 'loading';
  const length = issueLength(issue);
  const overLimit = length > MAX_ISSUE_CHARS;
  const selectedScenario = scenarios.find((scenario) => scenario.id === scenarioId);
  const viewingSaved = run.status === 'success' && run.restored;

  useEffect(() => {
    if (run.status === 'idle' && returningToDraft.current) {
      returningToDraft.current = false;
      scrollToSection('issue');
    }
    if (run.status !== 'success') return;
    outcome.current?.focus({ preventScroll: true });
    outcome.current?.scrollIntoView({ block: 'start', behavior: 'auto' });
  }, [run]);

  function returnToDraft() {
    returningToDraft.current = true;
    setRun({ status: 'idle' });
  }

  function openSaved(id: string) {
    if (inFlight.current) return;
    const entry = history.entries.find((item) => item.id === id);
    if (entry) setRun({ status: 'success', restored: true, ...entry });
  }

  function removeSaved() {
    if (inFlight.current) return;
    const notice = clearHistory();
    setHistory({ entries: notice ? history.entries : [], notice });
    if (!notice) document.getElementById('recent-summary')?.focus({ preventScroll: true });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || !acknowledged || length === 0 || overLimit) return;
    inFlight.current = true;
    const report = issue.trim();
    const submittedScenario = scenarioId;
    setRun({ status: 'loading', report });
    let response: DemoResponse;
    try {
      response = await investigate(report, submittedScenario);
    } catch (error) {
      setRun({ status: 'error', report, message: error instanceof Error ? error.message : 'Investigation failed. No diagnosis was received.' });
      return;
    } finally {
      inFlight.current = false;
    }
    const entry: SavedInvestigation = { id: crypto.randomUUID(), completedAt: new Date().toISOString(), report, scenarioId: submittedScenario, response };
    const entries = [entry, ...history.entries].slice(0, HISTORY_LIMIT);
    const notice = saveHistory(entries);
    setHistory({ entries, notice });
    setRun({ status: 'success', restored: false, ...entry });
  }

  return <>
    <a className="skip-link" href="#workspace">Skip to investigation</a>
    <header className="topbar">
      <a className="brand" href="#" aria-label="APILens home">
        <svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M4 11V5h6M22 5h6v6M28 21v6h-6M10 27H4v-6" /><circle cx="16" cy="16" r="6" /><path d="m20.5 20.5 3 3" /></svg>
        <span>API<span className="brand-accent">Lens</span></span>
      </a>
      <span className="environment-label"><span className="status-dot" aria-hidden="true" />Local portfolio demo</span>
    </header>

    <main id="workspace">
      <section className={`intro ${run.status === 'success' ? 'intro-complete' : ''}`} aria-labelledby="page-title">
        <div><p className="eyebrow">OBSERVE. INVESTIGATE. UNDERSTAND.</p>
          <h1 id="page-title">From bug report<br /> to <span>root cause.</span></h1>
          <p className="intro-copy">Give your API bug a closer look. A bounded AI agent gathers evidence from the contract, requests, logs, and implementation.</p>
        </div>
        <div className="intro-note"><span className="note-line" /><p>Real tools.<br />Observed evidence.<br /><strong>Your next move.</strong></p></div>
      </section>

      <RecentInvestigations history={{ ...history, notice: run.status === 'success' ? null : history.notice }} currentId={run.status === 'success' ? run.id : null} loading={loading} onOpen={openSaved} onClear={removeSaved} />

      <div className={`workspace-grid ${run.status === 'success' ? 'has-results' : ''}`}>
        <details key={viewingSaved ? `saved-report-${run.id}` : 'draft'} className="report-editor" open={run.status !== 'success'}>
          <summary hidden={run.status !== 'success'} onClick={(event) => { if (viewingSaved) { event.preventDefault(); returnToDraft(); } }}><span>{viewingSaved ? 'Return to draft' : 'Report & investigation controls'}</span><span>{viewingSaved ? 'Your unfinished report is preserved. Saved results are read-only.' : 'Expand to edit the report, select a scenario, or run again'}</span></summary>
        <section className="panel input-panel" aria-labelledby="input-title">
          <div className="panel-header"><div><p className="eyebrow">01 / THE REPORT</p><h2 id="input-title">What’s not working?</h2></div><span className="panel-icon" aria-hidden="true">↗</span></div>
          <form onSubmit={submit}>
            <fieldset disabled={loading} className="scenario-picker">
              <legend>Start with a sample scenario</legend>
              <div className="scenario-grid">{scenarios.map((scenario, index) => <button
                key={scenario.id} type="button" className="scenario-button"
                aria-pressed={scenarioId === scenario.id}
                onClick={() => { setIssue(scenario.issue); setScenarioId(scenario.id); setRun({ status: 'idle' }); }}
              ><span className="scenario-index">0{index + 1}</span><span><strong>{scenario.title}</strong><span className="scenario-category">{scenario.category}</span><code>{scenario.route}</code></span><span className="selection-dot" aria-hidden="true" /></button>)}</div>
            </fieldset>
            <div className="report-label"><label htmlFor="issue">Bug report <span aria-hidden="true">*</span></label><span className="mode-label">{selectedScenario ? 'Sample scenario' : 'Free-form'}</span></div>
            <textarea
              id="issue" value={issue} required disabled={loading} rows={7}
              aria-describedby="report-help character-count"
              aria-invalid={overLimit || undefined}
              placeholder="Describe the endpoint, what happened, and what you expected instead…"
              onChange={(event) => { setIssue(event.target.value); setScenarioId(null); setRun({ status: 'idle' }); }}
            />
            <div className="report-meta"><p id="report-help">{selectedScenario ? `${selectedScenario.title} fixtures selected. Editing switches to free-form.` : 'Free-form mode · fresh default fixtures. No scenario selected.'}</p><span id="character-count" className={overLimit ? 'over-limit' : ''}>{length.toLocaleString('en-US')} / 4,000</span></div>
            {overLimit && <p className="field-error" role="alert">Shorten the report to 4,000 characters before investigating.</p>}
            <div className="cost-notice">
              <span className="notice-label">LIVE REQUEST</span>
              <label className="cost-acknowledgment"><input type="checkbox" checked={acknowledged} disabled={loading} onChange={(event) => setAcknowledged(event.target.checked)} /><span>I understand this investigation uses OpenAI and may incur API costs.</span></label>
              <p>Credentials stay on the Agent Service. Nothing is sent until you start.</p>
            </div>
            <button className="submit-button" type="submit" disabled={loading || !acknowledged || length === 0 || overLimit}>{loading ? <><span className="spinner" aria-hidden="true" />Investigating…</> : <>Start investigation<span aria-hidden="true">→</span></>}</button>
            <p className="submit-hint">Each run starts with an isolated, disposable Sandbox.</p>
          </form>
          {run.status === 'error' && <div className="api-error" role="alert"><h3>Investigation unavailable</h3><p>{run.message}</p><p className="error-footnote">No automatic retries. A request may already have started; submitting again may incur another charge.</p></div>}
        </section>
        </details>

        <p className="sr-only" role="status">{loading ? 'Investigation in progress. The report is locked until completion.' : run.status === 'success' ? `${viewingSaved ? 'Saved investigation opened' : 'Investigation complete'}: ${run.response.diagnosis.status}. Outcome, metrics, evidence, and timeline are available.${viewingSaved ? ' Your draft is preserved. No new request was made.' : ''}` : ''}</p>
        <div key={run.status === 'success' ? `result-${run.id}` : run.status} id="result-navigation" tabIndex={-1} className="results-column" aria-busy={loading}>
          {run.status === 'success' && <ResultNavigation evidenceCount={run.response.diagnosis.evidence.length} stepCount={run.response.steps.length} hasTrace={run.response.trace_id !== null} historyCount={history.entries.length} viewingSaved={viewingSaved} onEdit={returnToDraft} />}
          {viewingSaved && <p className="saved-result-notice">Saved investigation · Completed <time dateTime={run.completedAt}>{new Date(run.completedAt).toLocaleString()}</time>. Viewing this result makes no new request.</p>}
          <div id="overview-panel" tabIndex={-1} className="result-overview">
          <section id="outcome-panel" ref={outcome} tabIndex={-1} className="panel outcome-panel" aria-labelledby="outcome-title">
            <div className="panel-header"><div><p className="eyebrow">02 / THE OUTCOME</p><h2 id="outcome-title">Investigation outcome</h2></div><span className="panel-caption">{viewingSaved ? 'Saved investigation' : run.status === 'success' ? 'Structured result' : 'Awaiting investigation'}</span></div>
            {run.status === 'success' && history.notice && <p className="history-notice outcome-history-notice" role="status">{history.notice}</p>}
            {run.status === 'success' ? <Outcome diagnosis={run.response.diagnosis} /> : <div className="diagnosis-empty"><span className="document-mark" aria-hidden="true">≡</span><div><h3>{loading ? 'Investigating your report' : 'Clarity comes from evidence.'}</h3><p>{loading ? 'The outcome appears when the bounded investigation finishes.' : 'The outcome, supporting evidence, and suggested fix appear here after the investigation.'}</p></div></div>}
          </section>
          {run.status === 'success' && <ExecutionMetrics metrics={run.response.metrics} />}
          </div>
          {run.status === 'success' && <section id="findings-panel" tabIndex={-1} className="panel diagnosis-panel" aria-labelledby="diagnosis-title">
            <div className="panel-header"><div><p className="eyebrow">03 / THE FINDINGS</p><h2 id="diagnosis-title">Diagnosis &amp; evidence</h2></div><span className="panel-caption">Observed support</span></div>
            <Diagnosis diagnosis={run.response.diagnosis} />
          </section>}
          {run.status !== 'idle' && <details className="submitted-report"><summary>Submitted report</summary><p>{run.report}</p></details>}
          {run.status === 'success' ? <details className="panel timeline-panel result-disclosure" open={run.response.steps.length === 0}>
            <summary id="timeline-summary"><div><p className="eyebrow">04 / THE INVESTIGATION</p><h2 id="timeline-title">Follow the evidence</h2><p>{run.response.steps.length} recorded steps · Expand the chronological investigation</p></div></summary>
            <Timeline steps={run.response.steps} />
          </details> : <section className="panel" aria-labelledby="timeline-title">
            <div className="panel-header"><div><p className="eyebrow">04 / THE INVESTIGATION</p><h2 id="timeline-title">Follow the evidence</h2></div><span className="panel-caption">Execution timeline</span></div>
            <div className="empty-state">
              <div className={`empty-orbit ${loading ? 'is-loading' : ''}`} aria-hidden="true"><span /><span /><span /></div>
              <h3>{loading ? 'Investigation in progress' : run.status === 'error' ? 'No results received' : 'Every step has a source'}</h3>
              <p>{loading ? 'The agent is gathering evidence. Actual steps will appear together when the request finishes.' : run.status === 'error' ? 'Resolve the error and explicitly start another investigation when you’re ready.' : 'Start an investigation to see which tools the agent actually used, in the order they ran.'}</p>
              {!loading && run.status === 'idle' && <span className="empty-footnote">No scripted sequence. No invented progress.</span>}
            </div>
          </section>}
          {run.status === 'success' && <>
            <TechnicalTrace key={run.response.trace_id} traceId={run.response.trace_id} />
            <a className="back-to-sections" href="#result-navigation" onClick={(event) => { event.preventDefault(); scrollToSection('result-navigation'); }}>Back to investigation sections ↑</a>
          </>}
        </div>
      </div>

      <footer className="page-footer"><p>APILens <span>/</span> Bounded tools. Observable decisions.</p><p>Local Sandbox only <span>·</span> Detailed traces stay in Phoenix</p></footer>
    </main>
  </>;
}
