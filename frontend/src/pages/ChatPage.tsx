import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Sparkles } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { Composer } from '../components/Composer'
import { AssistantMessage, UserMessage } from '../components/MessageView'
import { Picker } from '../components/Picker'
import { QuestionPanel } from '../components/QuestionPanel'
import { SourcePicker, type SourceSelection } from '../components/SourcePicker'
import { useConversation } from '../hooks/useConversation'
import { api } from '../lib/api'
import type { Block, Channel, DataSource, Domain, Message } from '../lib/types'

const SUGGESTIONS = [
  '最近一年的销售额趋势怎么样？有哪些明显的波动？',
  '对比各大区、各品类的销售额，哪里贡献最大？',
  '哪些品类的退款率最高？主要退款原因是什么？',
  '帮我出一份上个季度的经营分析报告',
]

function useSettingsData() {
  const channels = useQuery({ queryKey: ['channels'], queryFn: api.channels })
  const datasources = useQuery({ queryKey: ['datasources'], queryFn: api.datasources })
  const domains = useQuery({ queryKey: ['domains'], queryFn: api.domains })
  return { channels: channels.data, datasources: datasources.data, domains: domains.data }
}

function Pickers({
  channels,
  datasources,
  domains,
  channelId,
  sources,
  onChannel,
  onSources,
  disabled,
}: {
  channels: Channel[]
  datasources: DataSource[]
  domains: Domain[]
  channelId: string | null
  sources: SourceSelection
  onChannel: (id: string | null) => void
  onSources: (value: SourceSelection) => void
  disabled?: boolean
}) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <SourcePicker domains={domains} datasources={datasources} value={sources} onChange={onSources} disabled={disabled} />
      <Picker
        icon={<Bot size={13} />}
        value={channelId}
        placeholder="选择模型"
        options={channels.map((c) => ({
          value: c.id,
          label: c.supported ? `${c.name} · ${c.model}` : `${c.name}（暂不支持）`,
          disabled: !c.supported,
        }))}
        onChange={onChannel}
        disabled={disabled}
      />
    </div>
  )
}

const LAST_SOURCES_KEY = 'da:last-sources'

function loadLastSources(): SourceSelection | null {
  try {
    const raw = localStorage.getItem(LAST_SOURCES_KEY)
    return raw ? (JSON.parse(raw) as SourceSelection) : null
  } catch {
    return null
  }
}

function saveLastSources(value: SourceSelection) {
  try {
    localStorage.setItem(LAST_SOURCES_KEY, JSON.stringify(value))
  } catch {
    /* 隐私模式等情况下不可用，不影响使用 */
  }
}

function SetupHint({ channels, datasources }: { channels?: Channel[]; datasources?: DataSource[] }) {
  if (!channels || !datasources) return null
  const missing = [channels.length === 0 && '模型渠道', datasources.length === 0 && '数据源'].filter(Boolean)
  if (missing.length === 0) return null
  return (
    <div className="mb-3 rounded-xl bg-accent-soft px-4 py-3 text-sm text-ink-2">
      开始之前，请先在
      <Link to="/settings" className="mx-1 font-medium text-accent underline underline-offset-2">
        设置
      </Link>
      中添加{missing.join('和')}。
    </div>
  )
}

/** 新建分析：还没有会话，先选好数据源和模型，发出第一条消息时再创建会话 */
export function NewChatPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { channels, datasources, domains } = useSettingsData()
  const [channelId, setChannelId] = useState<string | null>(null)
  const [sources, setSources] = useState<SourceSelection | null>(null)

  useEffect(() => {
    if (channelId == null && channels?.length) {
      setChannelId((channels.find((c) => c.is_default && c.supported) ?? channels.find((c) => c.supported))?.id ?? null)
    }
  }, [channels, channelId])
  // 默认沿用上次的选择（过滤掉已删除的）；没有的话选第一个数据域，再不然第一个数据源
  useEffect(() => {
    if (sources != null || !datasources || !domains) return
    const last = loadLastSources()
    const ids = new Set(datasources.map((d) => d.id))
    if (last && (domains.some((d) => d.id === last.domainId) || last.datasourceIds.some((id) => ids.has(id)))) {
      setSources({
        domainId: domains.some((d) => d.id === last.domainId) ? last.domainId : null,
        datasourceIds: last.datasourceIds.filter((id) => ids.has(id)),
      })
    } else if (domains.length) {
      setSources({ domainId: domains[0].id, datasourceIds: [] })
    } else {
      setSources({ domainId: null, datasourceIds: datasources.length ? [datasources[0].id] : [] })
    }
  }, [datasources, domains, sources])

  const start = async (prompt: string) => {
    const selection = sources ?? { domainId: null, datasourceIds: [] }
    saveLastSources(selection)
    const conversation = await api.createConversation({
      channel_id: channelId,
      domain_id: selection.domainId,
      datasource_ids: selection.datasourceIds,
    })
    await queryClient.invalidateQueries({ queryKey: ['conversations'] })
    navigate(`/c/${conversation.id}`, { state: { prompt } })
  }
  const ready = !!channelId

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col items-center justify-center px-4 pb-10">
        <div className="w-full max-w-[760px]">
          <div className="mb-8 text-center">
            <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-2xl bg-accent-soft text-accent">
              <Sparkles size={22} />
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">今天想分析什么？</h1>
            <p className="mt-2 text-sm text-muted">用自然语言提问，AI 会查询数据、做分析、画图表，并可以整理成报告</p>
          </div>
          <SetupHint channels={channels} datasources={datasources} />
          <Pickers
            channels={channels ?? []}
            datasources={datasources ?? []}
            domains={domains ?? []}
            channelId={channelId}
            sources={sources ?? { domainId: null, datasourceIds: [] }}
            onChannel={setChannelId}
            onSources={setSources}
          />
          <Composer onSend={start} running={false} disabled={!ready} autoFocus />
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                disabled={!ready}
                onClick={() => void start(s)}
                className="rounded-xl px-3.5 py-2.5 text-left text-[13px] text-ink-2 shadow-[0_0_0_1px_var(--ring)] transition hover:bg-surface hover:text-ink disabled:opacity-50"
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

/** 最后一条助手消息里还没被回答的问题（之后没有新的用户消息） */
function pendingQuestion(messages: Message[]): Extract<Block, { kind: 'question' }> | null {
  const last = messages[messages.length - 1]
  if (!last || last.role !== 'assistant') return null
  for (let i = last.blocks.length - 1; i >= 0; i--) {
    const block = last.blocks[i]
    if (block.kind === 'question') return block
  }
  return null
}

export function ChatPage() {
  const { id } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { channels, datasources, domains } = useSettingsData()
  const { detail, loading, error, messages, live, running, send, stop } = useConversation(id)
  const [sendError, setSendError] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)
  const initialSent = useRef<string | null>(null)
  const [dismissed, setDismissed] = useState<string | null>(null)
  const question = running || live ? null : pendingQuestion(messages)
  const showQuestion = question != null && dismissed !== question.id

  // 从「新建分析」跳过来时带着第一条消息
  useEffect(() => {
    const prompt = (location.state as { prompt?: string } | null)?.prompt
    if (id && prompt && initialSent.current !== id && detail) {
      initialSent.current = id
      navigate(location.pathname, { replace: true, state: null })
      send(prompt).catch((err: Error) => setSendError(err.message))
    }
  }, [id, location.state, location.pathname, detail, navigate, send])

  // 用户停在底部时跟随新内容滚动；往上翻看时不打扰
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  })

  const patch = async (body: { channel_id?: string | null; datasource_ids?: string[]; domain_id?: string | null }) => {
    if (!id) return
    await api.patchConversation(id, body)
    await queryClient.invalidateQueries({ queryKey: ['conversation', id] })
  }

  if (error) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">会话不存在或已被删除</div>
  }

  return (
    <div className="flex h-full flex-col">
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
        }}
        className="scroll-thin flex-1 overflow-y-auto"
      >
        <div className="mx-auto w-full max-w-[860px] space-y-7 px-4 pt-16 pb-10 sm:px-6 md:pt-6">
          {loading ? <p className="text-center text-sm text-muted">加载中…</p> : null}
          {messages.map((m) =>
            m.role === 'user' ? (
              <UserMessage key={m.id} text={m.blocks[0]?.kind === 'text' ? m.blocks[0].text : ''} />
            ) : (
              <AssistantMessage key={m.id} blocks={m.blocks} status={m.status} usage={m.usage} />
            ),
          )}
          {live?.pendingUser ? <UserMessage text={live.pendingUser} /> : null}
          {live && running ? <AssistantMessage blocks={live.blocks} status="running" live statusText={live.status} /> : null}
          {live && !running && live.blocks.length ? <AssistantMessage blocks={live.blocks} status="completed" /> : null}
          {sendError ? <p className="text-sm text-bad">{sendError}</p> : null}
        </div>
      </div>
      <div className="mx-auto w-full max-w-[860px] px-4 pb-4 sm:px-6">
        {showQuestion ? (
          <QuestionPanel
            key={question.id}
            questions={question.questions}
            onDismiss={() => setDismissed(question.id)}
            onSubmit={async (text) => {
              setSendError('')
              stickRef.current = true
              try {
                await send(text)
              } catch (err) {
                setSendError((err as Error).message)
                throw err
              }
            }}
          />
        ) : null}
        {detail && !showQuestion ? (
          <Pickers
            channels={channels ?? []}
            datasources={datasources ?? []}
            channelId={detail.channel_id}
            domains={domains ?? []}
            sources={{ domainId: detail.domain_id, datasourceIds: detail.datasource_ids }}
            onChannel={(v) => void patch({ channel_id: v })}
            onSources={(v) => void patch({ domain_id: v.domainId, datasource_ids: v.datasourceIds })}
            disabled={running}
          />
        ) : null}
        <Composer
          running={running}
          onStop={() => void stop()}
          onSend={async (text) => {
            setSendError('')
            stickRef.current = true
            try {
              await send(text)
            } catch (err) {
              setSendError((err as Error).message)
              throw err
            }
          }}
          placeholder={showQuestion ? '也可以直接输入你的回答…' : '继续追问，例如：按月拆开看看？'}
        />
      </div>
    </div>
  )
}
