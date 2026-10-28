import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Loader2, Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { clsx } from 'clsx'
import { api } from '../../lib/api'
import type { Channel, ChannelInput, TestResult } from '../../lib/types'
import { Buttons, Empty, Field, Modal, Section, TestBadge, input } from './ui'

const PROTOCOL_DEFAULTS: Record<ChannelInput['protocol'], string> = {
  anthropic: 'https://api.anthropic.com',
  openai: 'https://api.openai.com/v1',
}

function ChannelForm({ channel, onDone }: { channel: Channel | null; onDone: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<ChannelInput>({
    name: channel?.name ?? '',
    protocol: channel?.protocol ?? 'anthropic',
    base_url: channel?.base_url ?? PROTOCOL_DEFAULTS.anthropic,
    api_key: '',
    model: channel?.model ?? '',
    is_default: channel?.is_default ?? false,
  })
  const [error, setError] = useState('')
  const save = useMutation({
    mutationFn: () => (channel ? api.updateChannel(channel.id, form) : api.createChannel(form)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['channels'] })
      onDone()
    },
    onError: (e: Error) => setError(e.message),
  })
  const set = <K extends keyof ChannelInput>(k: K, v: ChannelInput[K]) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <form onSubmit={(e) => { e.preventDefault(); save.mutate() }} className="space-y-3.5">
      <Field label="名称">
        <input className={input} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="如：Claude 官方" required />
      </Field>
      <Field group label="协议" hint={form.protocol === 'openai' ? '由 Codex 引擎驱动，渠道需支持 Responses API（/v1/responses）' : undefined}>
        <div className="flex gap-2">
          {(['anthropic', 'openai'] as const).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setForm((f) => ({ ...f, protocol: p, base_url: f.base_url === PROTOCOL_DEFAULTS[f.protocol] ? PROTOCOL_DEFAULTS[p] : f.base_url }))}
              className={clsx('flex-1 rounded-lg px-3 py-2 text-sm shadow-[0_0_0_1px_var(--hair)] transition', form.protocol === p ? 'bg-accent-soft font-medium text-accent shadow-[0_0_0_1.5px_var(--accent)]' : 'bg-surface text-ink-2')}
            >
              {p === 'anthropic' ? 'Anthropic 兼容' : 'OpenAI 兼容'}
            </button>
          ))}
        </div>
      </Field>
      <Field label="Base URL" hint={form.protocol === 'anthropic' ? '不含 /v1/messages，如 https://api.anthropic.com' : '含 /v1，如 https://api.openai.com/v1'}>
        <input className={input} value={form.base_url} onChange={(e) => set('base_url', e.target.value)} required />
      </Field>
      <Field label="API Key" hint={channel ? `当前：${channel.api_key_masked}，留空表示不修改` : undefined}>
        <input className={input} type="password" value={form.api_key} onChange={(e) => set('api_key', e.target.value)} required={!channel} autoComplete="off" />
      </Field>
      <Field label="模型">
        <input className={input} value={form.model} onChange={(e) => set('model', e.target.value)} placeholder="如：claude-sonnet-5-5" required />
      </Field>
      <label className="flex items-center gap-2 text-sm text-ink-2">
        <input type="checkbox" checked={form.is_default} onChange={(e) => set('is_default', e.target.checked)} className="accent-[var(--accent)]" />
        设为默认模型
      </label>
      {error ? <p className="text-sm text-bad">{error}</p> : null}
      <Buttons onCancel={onDone} saving={save.isPending} />
    </form>
  )
}

function ChannelRow({ channel, onEdit }: { channel: Channel; onEdit: () => void }) {
  const queryClient = useQueryClient()
  const [result, setResult] = useState<TestResult | null>(null)
  const test = useMutation({ mutationFn: () => api.testChannel(channel.id), onSuccess: setResult, onError: (e: Error) => setResult({ ok: false, message: e.message }) })
  const remove = useMutation({ mutationFn: () => api.deleteChannel(channel.id), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['channels'] }) })
  return (
    <div className="rounded-xl bg-surface px-4 py-3 shadow-[0_0_0_1px_var(--ring)]">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{channel.name}</span>
            {channel.is_default ? <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[11px] text-accent">默认</span> : null}
            {!channel.supported ? <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-muted">暂不支持</span> : null}
          </div>
          <div className="truncate text-xs text-muted">
            {channel.protocol === 'anthropic' ? 'Anthropic' : 'OpenAI'} · {channel.model} · {channel.base_url}
          </div>
        </div>
        <button onClick={() => test.mutate()} disabled={test.isPending} className="rounded-lg px-2.5 py-1.5 text-[13px] text-ink-2 hover:bg-surface-2 disabled:opacity-60">
          {test.isPending ? <Loader2 size={14} className="animate-spin" /> : '测试'}
        </button>
        <button onClick={onEdit} className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-ink" title="编辑"><Pencil size={14} /></button>
        <button onClick={() => confirm(`删除模型渠道「${channel.name}」？`) && remove.mutate()} className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-bad" title="删除"><Trash2 size={14} /></button>
      </div>
      <TestBadge result={result} />
    </div>
  )
}

export function ChannelsTab() {
  const channels = useQuery({ queryKey: ['channels'], queryFn: api.channels })
  const [edit, setEdit] = useState<Channel | null | undefined>(undefined)
  return (
    <>
      <Section icon={<Bot size={17} className="text-accent" />} title="模型渠道" desc="AI 使用的大模型。Anthropic 协议由 Claude Agent SDK 驱动，OpenAI 协议由 Codex SDK 驱动（需支持 Responses API）。" onAdd={() => setEdit(null)}>
        {channels.data?.map((c) => <ChannelRow key={c.id} channel={c} onEdit={() => setEdit(c)} />)}
        {channels.data?.length === 0 ? <Empty>还没有模型渠道</Empty> : null}
      </Section>
      {edit !== undefined ? (
        <Modal title={edit ? '编辑模型渠道' : '添加模型渠道'} onClose={() => setEdit(undefined)}>
          <ChannelForm channel={edit} onDone={() => setEdit(undefined)} />
        </Modal>
      ) : null}
    </>
  )
}
