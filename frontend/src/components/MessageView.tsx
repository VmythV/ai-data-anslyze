import { AlertCircle, Link2, MessageCircleQuestion } from 'lucide-react'
import { clsx } from 'clsx'
import { Link } from 'react-router-dom'
import { hasVisual, segment } from '../lib/blocks'
import type { Block, MessageStatus, Usage } from '../lib/types'
import { ChartCard } from './ChartCard'
import { KpiCards } from './KpiCards'
import { Markdown } from './Markdown'
import { ReportCard } from './ReportCard'
import { Steps } from './Steps'
import { TableCard } from './TableCard'

export function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-surface-2 px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap">
        {text}
      </div>
    </div>
  )
}

export function AssistantMessage({
  blocks,
  status,
  live = false,
  statusText = '',
  usage = null,
}: {
  blocks: Block[]
  status: MessageStatus
  live?: boolean
  statusText?: string
  usage?: Usage | null
}) {
  const segments = segment(blocks)
  const visual = hasVisual(blocks)
  const last = segments[segments.length - 1]
  const streamingText = live && last?.kind === 'block' && last.block.kind === 'text'

  return (
    <div className="space-y-3">
      {segments.map((seg, i) => {
        if (seg.kind === 'steps') return <Steps key={i} tools={seg.tools} live={live} />
        if (seg.kind === 'charts') {
          return (
            <div key={i} className={clsx('grid gap-3', seg.charts.length > 1 && 'lg:grid-cols-2')}>
              {seg.charts.map((c, j) => {
                // 奇数张时最后一张占满整行，不留半边空白
                const wide = seg.charts.length > 1 && seg.charts.length % 2 === 1 && j === seg.charts.length - 1
                return (
                  <div key={c.artifact_id} className={clsx('min-w-0', wide && 'lg:col-span-2')}>
                    <ChartCard title={c.title} option={c.option} compact={seg.charts.length > 1 && !wide} />
                  </div>
                )
              })}
            </div>
          )
        }
        const block = seg.block
        switch (block.kind) {
          case 'text':
            return <Markdown key={i} text={block.text} streaming={streamingText && i === segments.length - 1} />
          case 'table':
            return <TableCard key={i} block={block} preferCollapsed={visual} />
          case 'kpis':
            return <KpiCards key={i} title={block.title} items={block.items} />
          case 'report':
            return <ReportCard key={i} id={block.artifact_id} title={block.title} />
          case 'question':
            return (
              <div key={i} className="flex items-center gap-2 text-[13px] text-ink-2">
                <MessageCircleQuestion size={15} className="shrink-0 text-accent" />
                <span>
                  需要你确认：{block.questions.map((q) => q.header || q.question).join('、')}
                </span>
              </div>
            )
          case 'relation':
            return (
              <div key={i} className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl bg-accent-soft/60 px-3.5 py-2 text-[13px] text-ink-2">
                <Link2 size={15} className="shrink-0 text-accent" />
                <span>已提交关联建议</span>
                <code className="font-mono text-xs">{block.left}</code>
                <span className="text-muted">↔</span>
                <code className="font-mono text-xs">{block.right}</code>
                {block.match_rate != null ? <span className="text-muted">匹配 {(block.match_rate * 100).toFixed(0)}%</span> : null}
                <Link to="/settings?tab=relations" className="ml-auto text-xs text-accent underline-offset-2 hover:underline">
                  待管理员审核
                </Link>
              </div>
            )
          case 'error':
            return (
              <div key={i} className="flex gap-2 rounded-xl border border-bad/30 bg-bad/5 px-3.5 py-2.5 text-sm text-bad">
                <AlertCircle size={16} className="mt-0.5 shrink-0" />
                <span className="break-all">{block.message}</span>
              </div>
            )
        }
      })}
      {live && !streamingText ? <Thinking text={statusText} /> : null}
      {status === 'interrupted' || (usage && !live) ? (
        <div className="flex flex-wrap gap-x-3 text-xs text-muted">
          {status === 'interrupted' ? <span>已停止生成</span> : null}
          {usage && !live ? <UsageLine usage={usage} /> : null}
        </div>
      ) : null}
    </div>
  )
}

function Thinking({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted">
      <span className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <span key={i} className="size-1.5 animate-bounce rounded-full bg-muted" style={{ animationDelay: `${i * 120}ms` }} />
        ))}
      </span>
      {text || '思考中'}
    </div>
  )
}

function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function UsageLine({ usage }: { usage: Usage }) {
  const parts = [
    `用时 ${(usage.duration_ms / 1000).toFixed(1)} 秒`,
    `调用模型 ${usage.num_turns} 次`,
    `输入 ${tokens(usage.input_tokens)} / 输出 ${tokens(usage.output_tokens)} tokens`,
  ]
  const hit = usage.input_tokens ? Math.round((usage.cache_read_tokens / usage.input_tokens) * 100) : 0
  const title = [
    hit ? `其中缓存命中 ${tokens(usage.cache_read_tokens)}（${hit}%）` : '',
    usage.cost_usd ? `费用约 $${usage.cost_usd.toFixed(4)}（按 Claude 官方价格估算，经兼容网关时仅供参考）` : '',
  ]
    .filter(Boolean)
    .join('\n')
  return (
    <span title={title || undefined} className="tabular-nums">
      {parts.join(' · ')}
    </span>
  )
}
