import { useLayoutEffect, useRef } from 'react';

function reveal(id: string) {
  const target = document.getElementById(id);
  const disclosure = target?.closest('details');
  if (disclosure) disclosure.open = true;
  target?.focus({ preventScroll: true });
  return target;
}

export function scrollToSection(id: string) {
  reveal(id)?.scrollIntoView({
    block: 'start',
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
  });
}

// Native details retain their own disclosure state; navigation opens the destination.
export function ResultNavigation({ evidenceCount, stepCount, hasTrace, historyCount, viewingSaved, onEdit }: { evidenceCount: number; stepCount: number; hasTrace: boolean; historyCount: number; viewingSaved: boolean; onEdit: () => void }) {
  const navigation = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const bar = navigation.current;
    if (!bar) return;
    function measure() {
      if (!bar) return;
      const height = bar.getBoundingClientRect().height;
      // Leave most of the viewport for results, including with enlarged text.
      const fits = height <= window.innerHeight / 3;
      bar.parentElement?.style.setProperty('--section-offset', `${fits ? height + 32 : 20}px`);
      bar.dataset.fits = String(fits);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(bar);
    window.addEventListener('resize', measure);
    measure();
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); };
  }, []);
  const destinations = [
    ['overview-panel', 'Overview'],
    ...(evidenceCount > 0 ? [['evidence-summary', `Evidence (${evidenceCount})`]] : [['findings-panel', 'Findings']]),
    ['timeline-summary', `Timeline (${stepCount})`],
    ...(hasTrace ? [['trace-section', 'Phoenix']] : []),
    ...(historyCount > 0 ? [['recent-summary', `Recent (${historyCount})`]] : []),
  ];
  return <nav ref={navigation} className="result-navigation" aria-label="Investigation sections">
    <div className="result-links">{destinations.map(([id, label]) => <a key={id} href={`#${id}`} onClick={(event) => { event.preventDefault(); scrollToSection(id); }}>{label}</a>)}</div>
    <a className="edit-report-link" href="#issue" onClick={(event) => { event.preventDefault(); onEdit(); }}>{viewingSaved ? 'Return to draft' : 'Edit report / run again'} <span aria-hidden="true">↗</span></a>
  </nav>;
}
