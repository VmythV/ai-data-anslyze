import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, Network, Pencil, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { clsx } from 'clsx'
import { api } from '../../lib/api'
import { KINDS, kindLabel } from '../../lib/kinds'
import type { DataSource, DataSourceInput, DataSourceKind, TestResult } from '../../lib/types'
import { Badge, Buttons, Empty, Field, IconButton, Modal, Section, Spinner, TestBadge, input } from './ui'

const ALIAS = /^[a-z][a-z0-9_]{0,31}$/

/** 从库名推一个标识：小写、非字母数字换成下划线 */
function suggestAlias(database: string): string {
  const slug = database.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
  return /^[a-z]/.test(slug) ? slug.slice(0, 32) : ''
}

function KindOptions({ form, set }: { form: DataSourceInput; set: (options: DataSourceInput['options']) => void }) {
  const o = form.options
  const put = (key: string, value: string | boolean) => set({ ...o, [key]: value })
  if (form.kind === 'postgresql') {
    return (
      <Field label="SSL 模式">
        <select className={input} value={String(o.sslmode ?? '')} onChange={(e) => put('sslmode', e.target.value)}>
          <option value="">默认（prefer）</option>
          <option value="disable">disable</option>
          <option value="require">require</option>
          <option value="verify-full">verify-full</option>
        </select>
      </Field>
    )
  }
  if (form.kind === 'clickhouse') {
    return (
      <label className="flex items-center gap-2 text-sm text-ink-2">
        <input type="checkbox" checked={!!o.secure} onChange={(e) => put('secure', e.target.checked)} className="accent-[var(--accent)]" />
        使用 HTTPS（端口通常是 8443）
      </label>
    )
  }
  if (form.kind === 'mongodb') {
    return (
      <div className="grid grid-cols-2 gap-3">
        <Field label="认证库" hint="默认与数据库相同">
          <input className={input} value={String(o.auth_source ?? '')} onChange={(e) => put('auth_source', e.target.value)} placeholder="admin" />
        </Field>
        <Field label="副本集（可选）">
          <input className={input} value={String(o.replica_set ?? '')} onChange={(e) => put('replica_set', e.target.value)} />
        </Field>
        <label className="col-span-2 flex items-center gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={!!o.tls} onChange={(e) => put('tls', e.target.checked)} className="accent-[var(--accent)]" />
          使用 TLS
        </label>
      </div>
    )
  }
  return null
}

function DataSourceForm({ ds, onDone }: { ds: DataSource | null; onDone: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<DataSourceInput>({
    name: ds?.name ?? '',
    alias: ds?.alias ?? '',
    kind: ds?.kind ?? 'mysql',
    host: ds?.host ?? '',
    port: ds?.port ?? 3306,
    username: ds?.username ?? '',
    password: '',
    database: ds?.database ?? '',
    options: ds?.options ?? {},
    description: ds?.description ?? '',
  })
  const [aliasTouched, setAliasTouched] = useState(!!ds)
  const [error, setError] = useState('')
  const [result, setResult] = useState<TestResult | null>(null)
  const save = useMutation({
    mutationFn: () => (ds ? api.updateDataSource(ds.id, form) : api.createDataSource(form)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['datasources'] })
      // catalog 在后台同步，稍后刷新状态
      setTimeout(() => void queryClient.invalidateQueries({ queryKey: ['federation'] }), 3000)
      onDone()
    },
    onError: (e: Error) => setError(e.message),
  })
  const test = useMutation({
    mutationFn: () => api.testDataSource({ ...form, alias: form.alias || 'tmp', id: ds?.id }),
    onSuccess: setResult,
    onError: (e: Error) => setResult({ ok: false, message: e.message }),
  })
  const set = <K extends keyof DataSourceInput>(k: K, v: DataSourceInput[K]) => setForm((f) => ({ ...f, [k]: v }))
  const setKind = (kind: DataSourceKind) =>
    setForm((f) => {
      const oldDefault = KINDS.find((k) => k.value === f.kind)?.port
      const port = f.port === oldDefault ? (KINDS.find((k) => k.value === kind)?.port ?? f.port) : f.port
      return { ...f, kind, port, options: {} }
    })
  const aliasValid = ALIAS.test(form.alias)

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!aliasValid) return setError('标识只能用小写字母开头，包含小写字母、数字和下划线')
        save.mutate()
      }}
      className="space-y-3.5"
    >
      <Field group label="类型">
        <div className="grid grid-cols-4 gap-2">
          {KINDS.map((k) => (
            <button
              key={k.value}
              type="button"
              onClick={() => setKind(k.value)}
              className={clsx(
                'rounded-lg px-2 py-2 text-sm shadow-[0_0_0_1px_var(--hair)] transition',
                form.kind === k.value ? 'bg-accent-soft font-medium text-accent shadow-[0_0_0_1.5px_var(--accent)]' : 'bg-surface text-ink-2',
              )}
            >
              {k.label}
            </button>
          ))}
        </div>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="名称">
          <input className={input} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="如：会员营销库" required />
        </Field>
        <Field label="标识" hint="AI 用它指代这个数据源，如 crm">
          <input
            className={clsx(input, form.alias && !aliasValid && 'shadow-[0_0_0_1.5px_var(--bad)]')}
            value={form.alias}
            onChange={(e) => {
              setAliasTouched(true)
              set('alias', e.target.value)
            }}
            placeholder="crm"
            required
          />
        </Field>
      </div>
      <div className="grid grid-cols-[1fr_110px] gap-3">
        <Field label="主机">
          <input className={input} value={form.host} onChange={(e) => set('host', e.target.value)} placeholder="db.example.com" required />
        </Field>
        <Field label="端口">
          <input className={input} type="number" value={form.port} onChange={(e) => set('port', Number(e.target.value))} required />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="用户名" hint={form.kind === 'mongodb' ? '未开启认证可留空' : '建议使用只读账号'}>
          <input className={input} value={form.username} onChange={(e) => set('username', e.target.value)} required={form.kind !== 'mongodb'} autoComplete="off" />
        </Field>
        <Field label="密码" hint={ds ? '留空表示不修改' : undefined}>
          <input className={input} type="password" value={form.password} onChange={(e) => set('password', e.target.value)} autoComplete="new-password" />
        </Field>
      </div>
      <Field label="数据库">
        <input
          className={input}
          value={form.database}
          onChange={(e) => {
            const database = e.target.value
            setForm((f) => ({ ...f, database, alias: aliasTouched ? f.alias : suggestAlias(database) }))
          }}
          required
        />
      </Field>
      <KindOptions form={form} set={(options) => set('options', options)} />
      <Field label="业务说明（可选）" hint="告诉 AI 这个库的业务背景、指标口径、表之间的关系，能明显提升分析准确度">
        <textarea
          className={clsx(input, 'min-h-[88px] resize-y')}
          value={form.description}
          onChange={(e) => set('description', e.target.value)}
          placeholder={'例如：\n- GMV 统计 orders.pay_amount，只算「已完成」和「配送中」\n- 大区信息在 regions 表，通过 region_id 关联'}
        />
      </Field>
      <TestBadge result={result} />
      {error ? <p className="text-sm text-bad">{error}</p> : null}
      <Buttons
        onCancel={onDone}
        saving={save.isPending}
        extra={
          <button type="button" onClick={() => test.mutate()} disabled={test.isPending} className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-ink-2 shadow-[0_0_0_1px_var(--hair)] hover:bg-surface-2 disabled:opacity-60">
            {test.isPending ? <Spinner /> : null}测试连接
          </button>
        }
      />
    </form>
  )
}

function DataSourceRow({ ds, onEdit }: { ds: DataSource; onEdit: () => void }) {
  const queryClient = useQueryClient()
  const [result, setResult] = useState<TestResult | null>(null)
  const remove = useMutation({ mutationFn: () => api.deleteDataSource(ds.id), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['datasources'] }) })
  const refresh = useMutation({
    mutationFn: () => api.refreshSchema(ds.id),
    onSuccess: (r) => {
      setResult(r)
      void queryClient.invalidateQueries({ queryKey: ['datasources'] })
      void queryClient.invalidateQueries({ queryKey: ['schema', ds.id] })
    },
  })
  const refreshed = ds.schema_refreshed_at ? new Date(ds.schema_refreshed_at).toLocaleString('zh-CN', { hour12: false }) : null
  return (
    <div className="rounded-xl bg-surface px-4 py-3 shadow-[0_0_0_1px_var(--ring)]">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{ds.name}</span>
            <code className="rounded bg-surface-2 px-1.5 py-px font-mono text-[11px] text-ink-2">{ds.alias}</code>
            <Badge>{kindLabel(ds.kind)}</Badge>
          </div>
          <div className="truncate text-xs text-muted">
            {ds.username ? `${ds.username}@` : ''}
            {ds.host}:{ds.port}/{ds.database}
            {' · '}
            {ds.schema_error ? <span className="text-bad">表结构获取失败</span> : refreshed ? `表结构缓存于 ${refreshed}` : '表结构未缓存'}
          </div>
        </div>
        <button onClick={() => refresh.mutate()} disabled={refresh.isPending} className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13px] text-ink-2 hover:bg-surface-2 disabled:opacity-60" title="重新读取表结构">
          {refresh.isPending ? <Spinner /> : <RefreshCw size={13} />}
          刷新表结构
        </button>
        <IconButton onClick={onEdit} title="编辑"><Pencil size={14} /></IconButton>
        <IconButton danger title="删除" onClick={() => confirm(`删除数据源「${ds.name}」？引用它的数据域、关联关系会一并移除，使用它的会话将无法继续查询。`) && remove.mutate()}>
          <Trash2 size={14} />
        </IconButton>
      </div>
      <TestBadge result={result ?? (ds.schema_error ? { ok: false, message: ds.schema_error } : null)} />
    </div>
  )
}

function FederationBanner() {
  const { data } = useQuery({ queryKey: ['federation'], queryFn: api.federation, refetchInterval: 30_000 })
  if (!data) return null
  if (!data.enabled) {
    return (
      <p className="mb-4 rounded-xl bg-surface-2 px-4 py-2.5 text-[13px] text-muted">
        联邦查询未启用：不同引擎之间的数据只能分别查询后在 Python 里关联。部署时加上 <code className="font-mono">--profile federation</code> 并配置
        <code className="font-mono"> DA_TRINO_URL</code> 即可启用。
      </p>
    )
  }
  return (
    <div className={clsx('mb-4 flex items-start gap-2 rounded-xl px-4 py-2.5 text-[13px]', data.error ? 'bg-warn/10 text-warn' : 'bg-good/10 text-good')}>
      <Network size={15} className="mt-0.5 shrink-0" />
      <span>
        联邦查询已启用，{data.catalogs.length} 个数据源可跨引擎关联
        {data.catalogs.length ? `（${data.catalogs.join('、')}）` : ''}
        {data.error ? `；部分同步失败：${data.error}` : ''}
      </span>
    </div>
  )
}

export function DataSourcesTab() {
  const datasources = useQuery({ queryKey: ['datasources'], queryFn: api.datasources })
  const [edit, setEdit] = useState<DataSource | null | undefined>(undefined)
  return (
    <>
      <FederationBanner />
      <Section icon={<Database size={17} className="text-accent" />} title="数据源" desc="支持 MySQL、PostgreSQL、ClickHouse、MongoDB。AI 只执行只读查询，仍建议为它单独创建只读账号；密码加密保存。" onAdd={() => setEdit(null)}>
        {datasources.data?.map((d) => <DataSourceRow key={d.id} ds={d} onEdit={() => setEdit(d)} />)}
        {datasources.data?.length === 0 ? <Empty>还没有数据源</Empty> : null}
      </Section>
      {edit !== undefined ? (
        <Modal title={edit ? '编辑数据源' : '添加数据源'} onClose={() => setEdit(undefined)}>
          <DataSourceForm ds={edit} onDone={() => setEdit(undefined)} />
        </Modal>
      ) : null}
    </>
  )
}
