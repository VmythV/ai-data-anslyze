import { ChevronDown, Code2, Table2 } from 'lucide-react'
import { useState } from 'react'
import { clsx } from 'clsx'
import type { Block } from '../lib/types'

type TableBlock = Extract<Block, { kind: 'table' }>

function format(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
  }
  return String(value)
}

export function TableCard({ block, preferCollapsed = false }: { block: TableBlock; preferCollapsed?: boolean }) {
  const small = block.rows.length > 0 && block.rows.length <= 12
  // 用户点过之后以用户的为准；没点过时，回答里出现图表就自动收起
  const [toggled, setToggled] = useState<boolean | null>(null)
  const open = toggled ?? (small && !preferCollapsed)
  const setOpen = (value: boolean) => setToggled(value)
  const [showSql, setShowSql] = useState(false)
  const numeric = block.columns.map((_, i) => block.rows.length > 0 && block.rows.every((r) => r[i] == null || typeof r[i] === 'number'))
  const shown = block.rows.length

  return (
    <div className="overflow-hidden rounded-2xl bg-surface shadow-[0_0_0_1px_var(--ring)]">
      <div className="flex items-center gap-2 px-4 py-3">
        <button onClick={() => setOpen(!open)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <Table2 size={15} className="shrink-0 text-accent" />
          {block.datasource ? (
            <code className="shrink-0 rounded bg-surface-2 px-1.5 py-px font-mono text-[11px] text-ink-2">
              {block.datasource === 'federated' ? '联邦' : block.datasource}
            </code>
          ) : null}
          <span className="truncate text-sm font-medium">{block.title}</span>
          <span className="shrink-0 text-xs text-muted">
            {block.total_rows} 行 × {block.columns.length} 列{block.truncated ? '（已截断）' : ''}
          </span>
          <ChevronDown size={15} className={clsx('ml-auto shrink-0 text-muted transition', open && 'rotate-180')} />
        </button>
        <button
          onClick={() => setShowSql(!showSql)}
          className={clsx('shrink-0 rounded-md p-1.5 transition hover:bg-surface-2', showSql ? 'text-accent' : 'text-muted')}
          title={block.language === 'mongo' ? '查看查询' : '查看 SQL'}
        >
          <Code2 size={15} />
        </button>
      </div>
      {showSql ? (
        <pre className="scroll-thin mx-4 mb-3 overflow-x-auto rounded-lg bg-code px-3 py-2.5 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">
          {block.sql}
        </pre>
      ) : null}
      {open ? (
        <div className="scroll-thin max-h-[360px] overflow-auto border-t border-hair">
          {shown === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted">查询结果为空</p>
          ) : (
            <table className="w-full text-[13px] tabular-nums">
              <thead className="sticky top-0 bg-surface">
                <tr>
                  {block.columns.map((c, i) => (
                    <th
                      key={i}
                      className={clsx('border-b border-hair px-4 py-2 font-semibold whitespace-nowrap text-muted', numeric[i] ? 'text-right' : 'text-left')}
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r} className="hover:bg-surface-2">
                    {row.map((v, i) => (
                      <td key={i} className={clsx('border-b border-hair px-4 py-1.5 whitespace-nowrap', numeric[i] && 'text-right')}>
                        {format(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {block.total_rows > shown ? (
            <p className="px-4 py-2 text-xs text-muted">共 {block.total_rows} 行，这里展示前 {shown} 行</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
