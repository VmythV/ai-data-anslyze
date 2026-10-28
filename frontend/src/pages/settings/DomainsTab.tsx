import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Layers, Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { clsx } from 'clsx'
import { api } from '../../lib/api'
import { kindShort } from '../../lib/kinds'
import type { Domain, DomainInput } from '../../lib/types'
import { Badge, Buttons, Empty, Field, IconButton, Modal, Section, input } from './ui'

function DomainForm({ domain, onDone }: { domain: Domain | null; onDone: () => void }) {
  const queryClient = useQueryClient()
  const { data: datasources = [] } = useQuery({ queryKey: ['datasources'], queryFn: api.datasources })
  const [form, setForm] = useState<DomainInput>({
    name: domain?.name ?? '',
    description: domain?.description ?? '',
    datasource_ids: domain?.datasource_ids ?? [],
  })
  const [error, setError] = useState('')
  const save = useMutation({
    mutationFn: () => (domain ? api.updateDomain(domain.id, form) : api.createDomain(form)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['domains'] })
      onDone()
    },
    onError: (e: Error) => setError(e.message),
  })
  const toggle = (id: string) =>
    setForm((f) => ({
      ...f,
      datasource_ids: f.datasource_ids.includes(id) ? f.datasource_ids.filter((x) => x !== id) : [...f.datasource_ids, id],
    }))

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!form.datasource_ids.length) return setError('至少选择一个数据源')
        save.mutate()
      }}
      className="space-y-3.5"
    >
      <Field label="名称">
        <input className={input} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如：电商经营" required />
      </Field>
      <Field group label="包含的数据源">
        <div className="grid gap-1.5 sm:grid-cols-2">
          {datasources.map((d) => {
            const on = form.datasource_ids.includes(d.id)
            return (
              <button
                key={d.id}
                type="button"
                onClick={() => toggle(d.id)}
                className={clsx(
                  'flex items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition',
                  on ? 'bg-accent-soft shadow-[0_0_0_1.5px_var(--accent)]' : 'bg-surface shadow-[0_0_0_1px_var(--hair)]',
                )}
              >
                <input type="checkbox" readOnly checked={on} className="pointer-events-none accent-[var(--accent)]" />
                <span className="min-w-0 flex-1 truncate">{d.name}</span>
                <Badge>{kindShort(d.kind)}</Badge>
              </button>
            )
          })}
        </div>
      </Field>
      <Field label="说明（可选）" hint="这组数据源之间的业务关系、统一口径、注意事项。会写进 AI 的系统提示">
        <textarea
          className={clsx(input, 'min-h-[88px] resize-y')}
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder={'例如：\n- 客户维度以 shop.customers.id 为准，crm 会员号 = M + 8 位客户 ID\n- 行为日志只有 2025 年以后的数据'}
        />
      </Field>
      {error ? <p className="text-sm text-bad">{error}</p> : null}
      <Buttons onCancel={onDone} saving={save.isPending} />
    </form>
  )
}

export function DomainsTab() {
  const queryClient = useQueryClient()
  const domains = useQuery({ queryKey: ['domains'], queryFn: api.domains })
  const { data: datasources = [] } = useQuery({ queryKey: ['datasources'], queryFn: api.datasources })
  const [edit, setEdit] = useState<Domain | null | undefined>(undefined)
  const remove = useMutation({ mutationFn: api.deleteDomain, onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domains'] }) })
  const nameOf = (id: string) => datasources.find((d) => d.id === id)?.name ?? '已删除'

  return (
    <>
      <Section icon={<Layers size={17} className="text-accent" />} title="数据域" desc="把业务上相关的数据源编成一组，对话时整组选择。域里增减数据源后，已有会话也会跟着更新。" onAdd={() => setEdit(null)}>
        {domains.data?.map((d) => (
          <div key={d.id} className="flex items-start gap-3 rounded-xl bg-surface px-4 py-3 shadow-[0_0_0_1px_var(--ring)]">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{d.name}</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {d.datasource_ids.map((id) => <Badge key={id}>{nameOf(id)}</Badge>)}
              </div>
              {d.description ? <p className="mt-1.5 line-clamp-2 text-xs text-muted">{d.description}</p> : null}
            </div>
            <IconButton onClick={() => setEdit(d)} title="编辑"><Pencil size={14} /></IconButton>
            <IconButton danger title="删除" onClick={() => confirm(`删除数据域「${d.name}」？使用它的会话会失去这组数据源。`) && remove.mutate(d.id)}>
              <Trash2 size={14} />
            </IconButton>
          </div>
        ))}
        {domains.data?.length === 0 ? <Empty>还没有数据域</Empty> : null}
      </Section>
      {edit !== undefined ? (
        <Modal title={edit ? '编辑数据域' : '添加数据域'} onClose={() => setEdit(undefined)}>
          <DomainForm domain={edit} onDone={() => setEdit(undefined)} />
        </Modal>
      ) : null}
    </>
  )
}
