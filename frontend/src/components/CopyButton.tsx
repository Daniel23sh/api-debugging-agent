import { useState } from 'react';

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [feedback, setFeedback] = useState('');
  async function copy() {
    setFeedback('');
    try {
      await navigator.clipboard.writeText(text);
      setFeedback('Copied to clipboard.');
    } catch {
      setFeedback('Copy failed. Select and copy the text manually.');
    }
  }
  return <span className="copy-action">
    <button type="button" className="secondary-button" onClick={copy}>{label}</button>
    <span className="copy-feedback" role="status">{feedback}</span>
  </span>;
}
