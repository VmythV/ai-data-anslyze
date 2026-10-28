import { Check, ChevronDown, Database, Layers } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { clsx } from 'clsx'
import { kindShort } from '../lib/kinds'
import type { DataSource, Domain } from '../lib/types'

export interface SourceSelection {
  domainId: string | null
  datasourceIds: string[]
}

/** 选中的数据域里的数据源 ∪ 手动勾选的数据源（与后端 resolve_sources 一致） */
function effectiveSources(sel: SourceSelection, domains: Domain[]): Set<string> {
  const domain = domains.find((d) => d.id === sel.domainId)
  return new Set([...(domain?.datasource_ids ?? []), ...sel.datasourceIds])
}

function summary(sel: SourceSelection, domains: Domain[], datasources: DataSource[]): string {
  const domain = domains.find((d) => d.id === sel.domainId)
  const extra = sel.datasourceIds.filter((id) => !domain?.datasource_ids.includes(id))
  const names = extra.map((id) => datasources.find((d) => d.id === id)?.name).filter(Boolean) as string[]
  if (domain) return extra.length ? `${domain.name} + ${extra.length} 个数据源` : domain.name
  if (names.length === 0) return '选择数据源'
  return names.length <= 2 ? names.join('、') : `${names.slice(0, 2).join('、')} 等 ${names.length} 个`
}

export function SourcePicker({
  domains,
  datasources,
  value,
  onChange,
  disabled,
}: {
  domains: Domain[]
  datasources: DataSource[]
  value: SourceSelection
  onChange: (value: SourceSelection) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<'domains' | 'datasources'>(value.domainId || !value.datasourceIds.length ? 'domains' : 'datasources')
  const ref = useRef<HTMLDivElement>(null)
  const domain = domains.find((d) => d.id === value.domainId)
  const fromDomain = new Set(domain?.datasource_ids ?? [])
  const total = effectiveSources(value, domains).size

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const toggle = (id: string) =>
    onChange({
      ...value,
      datasourceIds: value.datasourceIds.includes(id) ? value.datasourceIds.filter((x) => x !== id) : [...value.datasourceIds, id],
    })

  return (
    <div ref={ref} className="relative">
      <button
        disabled={disabled}
        onClick={() => setOpen(!open)}
        className="inline-flex max-w-[280px] items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-[13px] text-ink-2 shadow-[0_0_0_1px_var(--ring)] transition hover:text-ink disabled:opacity-60"
      >
        {domain ? <Layers size={13} className="text-muted" /> : <Database size={13} className="text-muted" />}
        <span className="truncate">{summary(value, domains, datasources)}</span>
        {total > 1 ? <span className="rounded-full bg-surface-2 px-1.5 text-[11px] text-muted tabular-nums">{total}</span> : null}
        <ChevronDown size={13} className="shrink-0 text-muted" />
      </button>
      {open ? (
        <div className="absolute bottom-full left-0 z-30 mb-2 w-[340px] max-w-[calc(100vw-32px)] overflow-hidden rounded-xl bg-surface shadow-[0_0_0_1px_var(--ring),0_12px_32px_-8px_rgba(0,0,0,0.25)]">
          <div className="flex border-b border-hair px-2 pt-1.5">
            {(['domains', 'datasources'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={clsx('-mb-px border-b-2 px-3 py-1.5 text-[13px]', tab === t ? 'border-accent font-medium text-ink' : 'border-transparent text-muted hover:text-ink')}
              >
                {t === 'domains' ? `数据域 ${domains.length ? `(${domains.length})` : ''}` : `数据源 (${datasources.length})`}
              </button>
            ))}
          </div>
          <div className="scroll-thin max-h-[320px] overflow-y-auto p-1.5">
            {tab === 'domains' ? (
              <>
                {[{ id: null, name: '不使用数据域', description: '只用手动勾选的数据源', datasource_ids: [] as string[] }, ...domains].map((d) => {
                  const on = value.domainId === d.id
                  return (
                    <button
                      key={d.id ?? 'none'}
                      onClick={() => onChange({ ...value, domainId: d.id })}
                      className={clsx('flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition', on ? 'bg-accent-soft' : 'hover:bg-surface-2')}
                    >
                      <span className={clsx('mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full', on ? 'bg-accent' : 'shadow-[inset_0_0_0_1.5px_var(--muted)]')}>
                        {on ? <span className="size-1.5 rounded-full bg-accent-ink" /> : null}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm">{d.name}</span>
                        <span className="block truncate text-xs text-muted">
                          {d.id
                            ? d.datasource_ids.map((id) => datasources.find((x) => x.id === id)?.name).filter(Boolean).join('、')
                            : d.description}
                        </span>
                      </span>
                    </button>
                  )
                })}
                {!domains.length ? <p className="px-2.5 py-3 text-xs text-muted">还没有数据域，可以在「设置 → 数据域」里把相关的数据源编成一组</p> : null}
              </>
            ) : (
              datasources.map((d) => {
                const locked = fromDomain.has(d.id)
                const on = locked || value.datasourceIds.includes(d.id)
                return (
                  <button
                    key={d.id}
                    disabled={locked}
                    onClick={() => toggle(d.id)}
                    className={clsx('flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition', locked ? 'cursor-default' : 'hover:bg-surface-2')}
                  >
                    <span className={clsx('flex size-4 shrink-0 items-center justify-center rounded-[5px]', on ? 'bg-accent text-accent-ink' : 'shadow-[inset_0_0_0_1.5px_var(--muted)]', locked && 'opacity-50')}>
                      {on ? <Check size={11} strokeWidth={3} /> : null}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm">{d.name}</span>
                    {locked ? <span className="text-[11px] text-muted">来自数据域</span> : null}
                    <span className="rounded bg-surface-2 px-1.5 py-px text-[11px] text-muted">{kindShort(d.kind)}</span>
                  </button>
                )
              })
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}
