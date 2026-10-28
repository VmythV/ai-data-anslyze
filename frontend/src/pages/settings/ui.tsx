import { CheckCircle2, Loader2, Plus, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import { clsx } from 'clsx'
import type { TestResult } from '../../lib/types'

export const input =
  'w-full rounded-lg bg-surface px-3 py-2 text-sm shadow-[0_0_0_1px_var(--hair)] outline-none transition focus:shadow-[0_0_0_1.5px_var(--accent)] placeholder:text-muted'

/** 表单项。包着一组按钮时传 group：不能用 <label>，否则点标题会触发组里第一个按钮 */
export function Field({ label, hint, group, children }: { label: string; hint?: string; group?: boolean; children: ReactNode }) {
  const body = (
    <>
      <span className="mb-1 block text-[13px] font-medium text-ink-2">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-xs text-muted">{hint}</span> : null}
    </>
  )
  return group ? (
    <div role="group" aria-label={label} className="block">
      {body}
    </div>
  ) : (
    <label className="block">{body}</label>
  )
}

export function Modal({ title, children, onClose, wide }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 backdrop-blur-sm sm:pt-[8vh]" onClick={onClose}>
      <div className={clsx('w-full rounded-2xl bg-page p-5 shadow-2xl sm:p-6', wide ? 'max-w-2xl' : 'max-w-lg')} onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-4 text-lg font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  )
}

export function TestBadge({ result }: { result: TestResult | null }) {
  if (!result) return null
  return (
    <div className={clsx('mt-3 flex gap-2 rounded-lg px-3 py-2 text-[13px]', result.ok ? 'bg-good/10 text-good' : 'bg-bad/10 text-bad')}>
      {result.ok ? <CheckCircle2 size={15} className="mt-0.5 shrink-0" /> : <XCircle size={15} className="mt-0.5 shrink-0" />}
      <span className="break-all">{result.message}</span>
    </div>
  )
}

export function Buttons({ onCancel, saving, extra }: { onCancel: () => void; saving: boolean; extra?: ReactNode }) {
  return (
    <div className="mt-5 flex items-center gap-2">
      {extra}
      <span className="flex-1" />
      <button type="button" onClick={onCancel} className="rounded-lg px-3.5 py-2 text-sm text-ink-2 hover:bg-surface-2">
        取消
      </button>
      <button type="submit" disabled={saving} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink hover:opacity-90 disabled:opacity-60">
        {saving ? <Loader2 size={14} className="animate-spin" /> : null}保存
      </button>
    </div>
  )
}

export function Section({
  icon,
  title,
  desc,
  onAdd,
  actions,
  children,
}: {
  icon: ReactNode
  title: string
  desc: string
  onAdd: () => void
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="mb-10">
      <div className="mb-3 flex items-end gap-3">
        <div className="flex-1">
          <h2 className="flex items-center gap-2 text-base font-semibold">{icon}{title}</h2>
          <p className="mt-0.5 text-[13px] text-muted">{desc}</p>
        </div>
        {actions}
        <button onClick={onAdd} className="flex shrink-0 items-center gap-1 rounded-lg bg-surface px-3 py-1.5 text-[13px] font-medium shadow-[0_0_0_1px_var(--ring)] hover:bg-surface-2">
          <Plus size={14} />添加
        </button>
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-xl border border-dashed border-hair py-6 text-center text-sm text-muted">{children}</p>
}

export function Badge({ children, tone = 'muted' }: { children: ReactNode; tone?: 'muted' | 'accent' | 'good' | 'warn' }) {
  return (
    <span
      className={clsx(
        'rounded px-1.5 py-0.5 text-[11px] whitespace-nowrap',
        tone === 'accent' && 'bg-accent-soft text-accent',
        tone === 'muted' && 'bg-surface-2 text-muted',
        tone === 'good' && 'bg-good/10 text-good',
        tone === 'warn' && 'bg-warn/10 text-warn',
      )}
    >
      {children}
    </span>
  )
}

export function IconButton({ onClick, title, danger, children }: { onClick: () => void; title: string; danger?: boolean; children: ReactNode }) {
  return (
    <button onClick={onClick} title={title} className={clsx('rounded-lg p-1.5 text-muted hover:bg-surface-2', danger ? 'hover:text-bad' : 'hover:text-ink')}>
      {children}
    </button>
  )
}

export function Spinner() {
  return <Loader2 size={14} className="animate-spin" />
}
