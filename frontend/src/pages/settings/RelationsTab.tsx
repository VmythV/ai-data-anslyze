import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeftRight, Check, Link2, Loader2, Pencil, Sparkles, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { clsx } from 'clsx'
import { api } from '../../lib/api'
import type { Cardinality, DataSource, InferResult, Relation, RelationInput } from '../../lib/types'
import { Badge, Buttons, Empty, Field, IconButton, Modal, Section, input } from './ui'

const CARDINALITIES: { value: Cardinality; label: string }[] = [
  { value: 'n:1', label: '多对一' },
  { value: '1:1', label: '一对一' },
  { value: '1:n', label: '一对多' },
  { value: 'n:n', label: '多对多' },
]

/** 一侧的选择：数据源 → 表 → 字段，表和字段来自表结构缓存 */
function SideSelect({
  label,
  datasources,
  value,
  onChange,
}: {
  label: string
  datasources: DataSource[]
  value: { ds: string; table: string; column: string }
  onChange: (v: { ds: string; table: string; column: string }) => void
}) {
  const schema = useQuery({
    queryKey: ['schema', value.ds],
    queryFn: () => api.datasourceSchema(value.ds),
    enabled: !!value.ds,
  })
  const tables = schema.data ?? []
  const columns = tables.find((t) => t.name === value.table)?.columns ?? []
  return (
    <div className="space-y-2 rounded-xl bg-surface p-3 shadow-[0_0_0_1px_var(--hair)]">
      <div className="text-xs font-medium text-muted">{label}</div>
      <select className={input} value={value.ds} onChange={(e) => onChange({ ds: e.target.value, table: '', column: '' })} required>
        <option value="">选择数据源</option>
        {datasources.map((d) => <option key={d.id} value={d.id}>{d.name}（{d.alias}）</option>)}
      </select>
      <select className={input} value={value.table} onChange={(e) => onChange({ ...value, table: e.target.value, column: '' })} required disabled={!value.ds}>
        <option value="">{schema.isLoading ? '读取表结构…' : '选择表'}</option>
        {tables.map((t) => <option key={t.name} value={t.name}>{t.name}{t.comment ? ` · ${t.comment}` : ''}</option>)}
      </select>
      <select className={input} value={value.column} onChange={(e) => onChange({ ...value, column: e.target.value })} required disabled={!value.table}>
        <option value="">选择字段</option>
        {columns.map(([name, type, comment]) => (
          <option key={name} value={name}>{name}（{type}）{comment ? ` · ${comment}` : ''}</option>
        ))}
      </select>
      {schema.data && !tables.length ? <p className="text-xs text-warn">没有读到表结构，请在「数据源」页刷新</p> : null}
    </div>
  )
}

function RelationForm({ relation, onDone }: { relation: Relation | null; onDone: () => void }) {
  const queryClient = useQueryClient()
  const { data: datasources = [] } = useQuery({ queryKey: ['datasources'], queryFn: api.datasources })
  const [left, setLeft] = useState({ ds: relation?.left_datasource_id ?? '', table: relation?.left_table ?? '', column: relation?.left_column ?? '' })
  const [right, setRight] = useState({ ds: relation?.right_datasource_id ?? '', table: relation?.right_table ?? '', column: relation?.right_column ?? '' })
  const [cardinality, setCardinality] = useState<Cardinality>(relation?.cardinality ?? 'n:1')
  const [transform, setTransform] = useState(relation?.transform ?? '')
  const [description, setDescription] = useState(relation?.description ?? '')
  const [error, setError] = useState('')
  const body = (): RelationInput => ({
    left_datasource_id: left.ds, left_table: left.table, left_column: left.column,
    right_datasource_id: right.ds, right_table: right.table, right_column: right.column,
    cardinality, transform, description,
  })
  const save = useMutation({
    mutationFn: () => (relation ? api.updateRelation(relation.id, body()) : api.createRelation(body())),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['relations'] })
      onDone()
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <form onSubmit={(e) => { e.preventDefault(); save.mutate() }} className="space-y-3.5">
      <div className="grid gap-3 sm:grid-cols-2">
        <SideSelect label="一端" datasources={datasources} value={left} onChange={setLeft} />
        <SideSelect label="另一端" datasources={datasources} value={right} onChange={setRight} />
      </div>
      <Field group label="对应关系" hint="从「一端」看「另一端」：多对一 = 一端的多行对应另一端的一行">
        <div className="grid grid-cols-4 gap-2">
          {CARDINALITIES.map((c) => (
            <button
              key={c.value}
              type="button"
              onClick={() => setCardinality(c.value)}
              className={clsx(
                'rounded-lg px-2 py-1.5 text-sm shadow-[0_0_0_1px_var(--hair)] transition',
                cardinality === c.value ? 'bg-accent-soft font-medium text-accent shadow-[0_0_0_1.5px_var(--accent)]' : 'bg-surface text-ink-2',
              )}
            >
              {c.label}
            </button>
          ))}
        </div>
      </Field>
      <Field label="取值转换（可选）" hint="两端的值格式不同时写清楚，如「去掉前缀 M 后转成整数」">
        <input className={input} value={transform} onChange={(e) => setTransform(e.target.value)} />
      </Field>
      <Field label="备注（可选）">
        <input className={input} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="如：约 15% 的电商客户没有注册会员" />
      </Field>
      {error ? <p className="text-sm text-bad">{error}</p> : null}
      <Buttons onCancel={onDone} saving={save.isPending} />
    </form>
  )
}

function InferResultPanel({ result, onClose }: { result: InferResult; onClose: () => void }) {
  const ok = result.candidates.filter((c) => c.match_rate != null && c.match_rate >= 0.5)
  const rejected = result.candidates.filter((c) => !ok.includes(c))
  return (
    <div className="rounded-xl bg-surface px-4 py-3 text-[13px] shadow-[0_0_0_1px_var(--ring)]">
      <div className="mb-1.5 flex items-center gap-2">
        <Sparkles size={14} className="text-accent" />
        <span className="font-medium">
          核对了 {result.evaluated} 个候选，{result.created} 条匹配率达标，已加入待审核
        </span>
        <button onClick={onClose} className="ml-auto text-xs text-muted hover:text-ink">收起</button>
      </div>
      {result.evaluated === 0 ? <p className="text-muted">没有找到新的候选：已有的关联都配置过了，或字段注释、命名里没有线索。</p> : null}
      {rejected.length ? (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">{rejected.length} 个候选未达标（匹配率低于 50% 或核对失败）</summary>
          <ul className="mt-1 space-y-0.5 font-mono">
            {rejected.map((c, i) => (
              <li key={i}>
                {c.left} → {c.right}：{c.error || `匹配 ${((c.match_rate ?? 0) * 100).toFixed(0)}%`}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  )
}

export function RelationsTab() {
  const queryClient = useQueryClient()
  const [inferResult, setInferResult] = useState<InferResult | null>(null)
  const infer = useMutation({
    mutationFn: () => api.inferRelations(),
    onSuccess: (r) => {
      setInferResult(r)
      void queryClient.invalidateQueries({ queryKey: ['relations'] })
    },
  })
  const relations = useQuery({ queryKey: ['relations'], queryFn: api.relations })
  const { data: datasources = [] } = useQuery({ queryKey: ['datasources'], queryFn: api.datasources })
  const [edit, setEdit] = useState<Relation | null | undefined>(undefined)
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['relations'] })
  const remove = useMutation({ mutationFn: api.deleteRelation, onSuccess: invalidate })
  const confirmIt = useMutation({ mutationFn: api.confirmRelation, onSuccess: invalidate })
  const alias = (id: string) => datasources.find((d) => d.id === id)?.alias ?? '?'
  const pending = relations.data?.filter((r) => r.status === 'pending') ?? []
  const confirmed = relations.data?.filter((r) => r.status === 'confirmed') ?? []

  const row = (r: Relation) => (
    <div key={r.id} className="flex items-start gap-3 rounded-xl bg-surface px-4 py-3 shadow-[0_0_0_1px_var(--ring)]">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[13px]">
          <span>{alias(r.left_datasource_id)}.{r.left_table}.<b className="font-semibold">{r.left_column}</b></span>
          <ArrowLeftRight size={13} className="text-muted" />
          <span>{alias(r.right_datasource_id)}.{r.right_table}.<b className="font-semibold">{r.right_column}</b></span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
          <Badge>{CARDINALITIES.find((c) => c.value === r.cardinality)?.label}</Badge>
          {r.source === 'ai' ? <Badge tone="accent">AI 推断</Badge> : null}
          {r.status === 'pending' ? <Badge tone="warn">待审核</Badge> : null}
          {r.match_rate != null ? <span>匹配率 {(r.match_rate * 100).toFixed(1)}%</span> : null}
          {[r.transform, r.description].filter(Boolean).join('；')}
        </div>
      </div>
      {r.status === 'pending' ? (
        <button onClick={() => confirmIt.mutate(r.id)} className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13px] text-good hover:bg-good/10" title="采纳">
          <Check size={14} />采纳
        </button>
      ) : null}
      <IconButton onClick={() => setEdit(r)} title="编辑"><Pencil size={14} /></IconButton>
      <IconButton danger title="删除" onClick={() => confirm('删除这条关联？') && remove.mutate(r.id)}><Trash2 size={14} /></IconButton>
    </div>
  )

  return (
    <>
      <Section
        icon={<Link2 size={17} className="text-accent" />}
        title="关联关系"
        desc="不同数据源（或没有外键的表）之间靠哪些字段关联。会话里两端的数据源都被选中时，这些关系会告诉 AI。"
        onAdd={() => setEdit(null)}
        actions={
          <button
            onClick={() => infer.mutate()}
            disabled={infer.isPending}
            title="根据字段注释、同名字段和命名约定找候选，再抽样核对取值匹配率"
            className="flex shrink-0 items-center gap-1 rounded-lg bg-surface px-3 py-1.5 text-[13px] font-medium shadow-[0_0_0_1px_var(--ring)] hover:bg-surface-2 disabled:opacity-60"
          >
            {infer.isPending ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} className="text-accent" />}
            {infer.isPending ? '推断中…' : '推断关联'}
          </button>
        }
      >
        {inferResult ? <InferResultPanel result={inferResult} onClose={() => setInferResult(null)} /> : null}
        {infer.error ? <p className="text-sm text-bad">{(infer.error as Error).message}</p> : null}
        {pending.length ? (
          <>
            <div className="pt-1 text-xs font-medium text-warn">待审核（AI 推断，采纳后对所有会话生效；对话中提交的，采纳前只在提交它的会话里可用）</div>
            {pending.map(row)}
            <div className="pt-3 text-xs font-medium text-muted">已确认</div>
          </>
        ) : null}
        {confirmed.map(row)}
        {relations.data?.length === 0 ? <Empty>还没有关联关系</Empty> : null}
      </Section>
      {edit !== undefined ? (
        <Modal wide title={edit ? '编辑关联关系' : '添加关联关系'} onClose={() => setEdit(undefined)}>
          <RelationForm relation={edit} onDone={() => setEdit(undefined)} />
        </Modal>
      ) : null}
    </>
  )
}
