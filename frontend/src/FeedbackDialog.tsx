import { Check, LoaderCircle, Send, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { accountApi, type Account } from './accountApi';
import './FeedbackDialog.css';

/** The four kinds the server accepts; the wording matches `itp/feedback.py`. */
const KINDS = ['功能建议', '问题反馈', '界面体验', '其他'] as const;
type Kind = (typeof KINDS)[number];

const BODY_MIN = 5;
const BODY_MAX = 1000;
const CONTACT_MAX = 80;

/**
 * The feedback entry behind the top bar's 反馈 button.  It is a native
 * `<dialog>`: a centred card on a desktop and a bottom drawer on a phone.  A
 * visitor may report a problem without signing in — the account is attached
 * from the token by the server, never typed into this form.
 */
export function FeedbackDialog({ open, user, onClose }: {
  open: boolean; user: Account | null; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [kind, setKind] = useState<Kind>('功能建议');
  const [body, setBody] = useState('');
  const [contact, setContact] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);

  useEffect(() => {
    // The account's own contact is a suggestion, and stays editable.
    if (open) { setContact(user?.contact || ''); setError(''); }
  }, [open, user?.contact]);

  const problem = body.trim().length < BODY_MIN ? `反馈内容至少 ${BODY_MIN} 个字` : '';

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (problem) { setError(problem); return; }
    setBusy(true); setError('');
    try {
      await accountApi('/api/feedback', 'POST', {
        kind, body: body.trim(), contact: contact.trim(), page: window.location.pathname,
      });
      setSent(true);
      setBody('');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <dialog ref={dialog} className="feedback-dialog" aria-label="意见反馈"
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}
    onClick={(event) => { if (event.target === dialog.current && !busy) onClose(); }}>
    {/* Nothing is rendered while it is closed: a hidden copy of the account's
        name and contact would otherwise linger in the accessibility tree. */}
    {!open ? null : <>
    <div className="feedback-heading">
      <div><strong>意见反馈</strong>
        <small>告诉我们哪里不好用、缺什么功能；我们会按提交顺序查看。</small></div>
      <button type="button" aria-label="关闭反馈" disabled={busy} onClick={onClose}><X size={18} /></button>
    </div>

    {sent ? <div className="feedback-done" role="status">
      <Check size={26} aria-hidden="true" />
      <strong>反馈已提交</strong>
      <p>{user ? `已关联账号 ${user.display_name || user.name}，` : ''}
        我们会认真阅读每一条反馈。如果留了联系方式，必要时会与你联系。</p>
      <div className="feedback-actions">
        <button type="button" className="button" onClick={() => setSent(false)}>再提一条</button>
        <button type="button" className="button primary" onClick={onClose}>完成</button>
      </div>
    </div> : <form className="feedback-form" onSubmit={submit}>
      <fieldset className="feedback-kinds">
        <legend>反馈类型</legend>
        <div role="radiogroup" aria-label="反馈类型">
          {KINDS.map((option) => <button type="button" key={option} role="radio"
            aria-checked={kind === option} className={kind === option ? 'active' : ''}
            onClick={() => setKind(option)}>{option}</button>)}
        </div>
      </fieldset>

      <label className="feedback-field">
        <span>反馈内容 <em>必填</em></span>
        <textarea className="text-input" required rows={5} maxLength={BODY_MAX}
          placeholder="请描述遇到的问题或想要的功能，越具体越好" aria-label="反馈内容"
          aria-describedby="feedback-body-note"
          value={body} onChange={(event) => setBody(event.target.value)} />
        <small className="feedback-count" id="feedback-body-note">
          <span className={problem && body ? 'feedback-too-short' : undefined}>
            {problem && body ? problem : `至少 ${BODY_MIN} 个字`}</span>
          <span>{body.trim().length} / {BODY_MAX}</span></small>
      </label>

      <label className="feedback-field">
        <span>联系方式 <em>选填</em></span>
        <input className="text-input" maxLength={CONTACT_MAX} inputMode="tel"
          placeholder="手机号或邮箱，方便我们回复你" aria-label="联系方式"
          value={contact} onChange={(event) => setContact(event.target.value)} />
      </label>

      <p className="feedback-identity">
        {user ? <>将以账号 <strong>{user.display_name || user.name}</strong>（{user.name}）提交。</>
          : '当前未登录：可以直接提交，登录后提交会关联你的账号，便于我们回复。'}
      </p>
      {error && <p className="feedback-error" role="alert">{error}</p>}
      <div className="feedback-actions">
        <button type="button" className="button" disabled={busy} onClick={onClose}>取消</button>
        <button type="submit" className="button primary" disabled={busy}>
          {busy ? <><LoaderCircle size={15} className="spin" /> 正在提交…</> : <><Send size={15} /> 提交反馈</>}
        </button>
      </div>
    </form>}
    </>}
  </dialog>;
}
