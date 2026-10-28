import {
  BookOpen,
  Check,
  ChevronRight,
  Database,
  FileCode2,
  FileText,
  FolderSearch,
  Gauge,
  LineChart,
  Loader2,
  Link2,
  Minus,
  Network,
  Search,
  Table,
  Terminal,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { clsx } from 'clsx'
import type { Block } from '../lib/types'

type ToolBlock = Extract<Block, { kind: 'tool' }>

const str = (v: unknown) => (typeof v === 'string' ? v : '')

function describe(tool: ToolBlock): { icon: typeof Database; label: string; detail?: string } {
  const input = tool.input
  // 多数据源时在说明前带上数据源标识
  const on = (text: string) => (str(input.datasource) ? `${str(input.datasource)} · ${text}` : text)
  switch (tool.name) {
    case 'search_schema':
      return { icon: Search, label: '搜索表结构', detail: (input.keywords as string[] | undefined)?.join('、') }
    case 'list_tables':
      return { icon: Database, label: '查看数据表', detail: str(input.datasource) }
    case 'describe_table':
      return { icon: Table, label: '查看表结构', detail: on((input.tables as string[] | undefined)?.join('、') ?? '') }
    case 'run_sql':
      return { icon: Database, label: '执行查询', detail: on(str(input.purpose)) }
    case 'run_mongo':
      return { icon: Database, label: '执行查询', detail: on(str(input.purpose)) }
    case 'run_federated_sql':
      return { icon: Network, label: '联邦查询', detail: str(input.purpose) }
    case 'propose_relation': {
      const side = (v: unknown) => {
        const o = (v ?? {}) as Record<string, unknown>
        return `${str(o.datasource)}.${str(o.table)}.${str(o.column)}`
      }
      return { icon: Link2, label: '提交关联建议', detail: `${side(input.left)} ↔ ${side(input.right)}` }
    }
    case 'run_python':
      return { icon: Terminal, label: '运行 Python', detail: str(input.purpose) }
    case 'render_chart':
      return { icon: LineChart, label: '绘制图表', detail: str(input.title) }
    case 'render_kpis':
      return { icon: Gauge, label: '展示指标卡', detail: str(input.title) }
    case 'publish_report':
      return { icon: FileText, label: '生成报告', detail: str(input.title) }
    case 'Skill':
      return { icon: BookOpen, label: '加载技能', detail: str(input.skill).replace(/^analyst:/, '') }
    case 'Read':
      return { icon: FileCode2, label: '读取文件', detail: str(input.file_path) }
    case 'Write':
    case 'Edit':
      return { icon: FileCode2, label: '写入文件', detail: str(input.file_path) }
    case 'Glob':
    case 'Grep':
      return { icon: FolderSearch, label: '查找文件', detail: str(input.pattern) }
    default:
      return { icon: Terminal, label: tool.name }
  }
}

function StatusIcon({ status }: { status: ToolBlock['status'] }) {
  if (status === 'running') return <Loader2 size={13} className="animate-spin text-accent" />
  if (status === 'error') return <X size={13} className="text-bad" />
  if (status === 'interrupted') return <Minus size={13} className="text-muted" />
  return <Check size={13} className="text-good" />
}

function Code({ children }: { children: string }) {
  return (
    <pre className="scroll-thin max-h-72 overflow-auto rounded-lg bg-code px-3 py-2.5 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">
      {children}
    </pre>
  )
}

function StepDetail({ tool }: { tool: ToolBlock }) {
  const input = tool.input
  const body =
    tool.name === 'run_sql' || tool.name === 'run_federated_sql'
      ? str(input.sql)
      : tool.name === 'run_python'
        ? str(input.code)
        : tool.name === 'run_mongo'
          ? `db.${str(input.collection)}.aggregate(${JSON.stringify(input.pipeline, null, 2)})`
          : JSON.stringify(input, null, 2)
  const showInput = !['list_tables', 'Skill'].includes(tool.name) && body && body !== '{}'
  return (
    <div className="mt-1.5 mb-2 ml-6 space-y-2">
      {showInput ? <Code>{body}</Code> : null}
      {tool.output ? (
        <div>
          <div className="mb-1 text-[11px] font-medium tracking-wide text-muted">输出</div>
          <Code>{tool.output}</Code>
        </div>
      ) : null}
    </div>
  )
}

function Step({ tool }: { tool: ToolBlock }) {
  const [open, setOpen] = useState(false)
  const { icon: Icon, label, detail } = describe(tool)
  return (
    <li>
      <button
        onClick={() => setOpen(!open)}
        className="group flex w-full items-center gap-2 rounded-md py-1 text-left text-[13px] text-ink-2 hover:text-ink"
      >
        <span className="flex w-4 justify-center">
          <StatusIcon status={tool.status} />
        </span>
        <Icon size={14} className="shrink-0 text-muted" />
        <span className="shrink-0 font-medium">{label}</span>
        {detail ? <span className="truncate text-muted">{detail}</span> : null}
        <ChevronRight size={13} className={clsx('ml-auto shrink-0 text-muted opacity-0 transition group-hover:opacity-100', open && 'rotate-90 opacity-100')} />
      </button>
      {open ? <StepDetail tool={tool} /> : null}
    </li>
  )
}

export function Steps({ tools, live }: { tools: ToolBlock[]; live: boolean }) {
  if (tools.length === 1) {
    return (
      <ul className="px-1">
        <Step tool={tools[0]} />
      </ul>
    )
  }
  return <StepGroup tools={tools} live={live} />
}

function StepGroup({ tools, live }: { tools: ToolBlock[]; live: boolean }) {
  const runningNow = tools.some((t) => t.status === 'running')
  const [expanded, setExpanded] = useState<boolean | null>(null)
  const open = expanded ?? (live && runningNow)
  const failed = tools.filter((t) => t.status === 'error').length
  const current = [...tools].reverse().find((t) => t.status === 'running')

  return (
    <div className="px-1">
      <button onClick={() => setExpanded(!open)} className="flex w-full items-center gap-2 py-1 text-left text-[13px] text-muted hover:text-ink">
        <ChevronRight size={14} className={clsx('transition', open && 'rotate-90')} />
        {current ? (
          <span className="flex min-w-0 items-center gap-1.5 text-ink-2">
            <Loader2 size={13} className="shrink-0 animate-spin text-accent" />
            <span className="truncate">
              {describe(current).label}
              {describe(current).detail ? ` · ${describe(current).detail}` : ''}
            </span>
          </span>
        ) : (
          <span>
            {tools.length} 个分析步骤
            {failed ? <span className="text-bad"> · {failed} 个出错</span> : null}
          </span>
        )}
      </button>
      {open ? (
        <ul className="ml-[7px] border-l border-hair pb-1 pl-3">
          {tools.map((t) => (
            <Step key={t.id} tool={t} />
          ))}
        </ul>
      ) : null}
    </div>
  )
}
