import { clsx } from 'clsx'
import type { Kpi } from '../lib/types'

const ARROW = { good: '▲', bad: '▼', neutral: '' } as const

/** 指标卡：大号数字 + 变化 + 备注。数字多的时候自动换行，手机上两列 */
export function KpiCards({ title, items }: { title: string; items: Kpi[] }) {
  return (
    <div>
      {title ? <div className="mb-2 text-[13px] font-medium text-ink-2">{title}</div> : null}
      <div className={clsx('grid gap-3', items.length === 1 ? 'grid-cols-1 sm:max-w-xs' : 'grid-cols-2', items.length >= 3 && 'md:grid-cols-3', items.length === 4 && 'md:grid-cols-4')}>
        {items.map((k, i) => (
          <div key={i} className="rounded-2xl bg-surface px-4 py-3.5 shadow-[0_0_0_1px_var(--ring)]">
            <div className="truncate text-[13px] text-ink-2">{k.label}</div>
            <div className="mt-1 text-2xl leading-tight font-semibold tracking-tight tabular-nums sm:text-[26px]">{k.value}</div>
            {k.delta ? (
              <div className={clsx('mt-1 text-[13px] font-medium', k.tone === 'good' ? 'text-good' : k.tone === 'bad' ? 'text-bad' : 'text-ink-2')}>
                {ARROW[k.tone] ? `${ARROW[k.tone]} ` : ''}
                {k.delta}
              </div>
            ) : null}
            {k.note ? <div className="mt-0.5 text-xs leading-snug text-muted">{k.note}</div> : null}
          </div>
        ))}
      </div>
    </div>
  )
}
