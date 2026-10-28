import { Check, ChevronLeft, ChevronRight, MessageCircleQuestion, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { clsx } from 'clsx'
import type { Question } from '../lib/types'

interface Answer {
  selected: string[]
  other: boolean
  otherText: string
}

const EMPTY: Answer = { selected: [], other: false, otherText: '' }
const RECOMMENDED = /[（(]推荐[)）]\s*$/

function splitLabel(label: string): { text: string; recommended: boolean } {
  return { text: label.replace(RECOMMENDED, '').trim(), recommended: RECOMMENDED.test(label) }
}

function answered(a: Answer): boolean {
  return a.selected.length > 0 || (a.other && a.otherText.trim().length > 0)
}

/** 把选择整理成一条消息：模型能对上自己问的问题，用户在对话里看着也清楚 */
function formatAnswers(questions: Question[], answers: Answer[]): string {
  const lines = ['我的选择：']
  questions.forEach((q, i) => {
    const a = answers[i]
    const picked = a.selected.map((label) => splitLabel(label).text)
    if (a.other && a.otherText.trim()) picked.push(`其他：${a.otherText.trim()}`)
    lines.push(`- ${q.question}`, `  → ${picked.join('、')}`)
  })
  return lines.join('\n')
}

/**
 * AI 用 ask_user 提出的问题：显示在输入框上方。单选点一下就跳到下一题，多选可以勾多个；
 * 每题最后都有「其他」，可以自己填写。数字键 1~9 可以直接选当前题的选项。
 */
export function QuestionPanel({
  questions,
  onSubmit,
  onDismiss,
}: {
  questions: Question[]
  onSubmit: (text: string) => Promise<void>
  onDismiss: () => void
}) {
  const [answers, setAnswers] = useState<Answer[]>(() => questions.map(() => EMPTY))
  const [active, setActive] = useState(0)
  const [sending, setSending] = useState(false)
  const otherRef = useRef<HTMLInputElement>(null)
  const q = questions[active]
  const a = answers[active]
  const done = answers.every(answered)
  const last = active === questions.length - 1

  const update = (index: number, next: Answer) => setAnswers((prev) => prev.map((x, i) => (i === index ? next : x)))

  const advance = (from: number, nextAnswers: Answer[]) => {
    // 单选选完自动去下一道还没答的题
    const next = nextAnswers.findIndex((x, i) => i > from && !answered(x))
    if (next >= 0) setTimeout(() => setActive(next), 160)
  }

  const pick = (label: string) => {
    if (q.multi_select) {
      const selected = a.selected.includes(label) ? a.selected.filter((l) => l !== label) : [...a.selected, label]
      update(active, { ...a, selected })
    } else {
      const next = { ...a, selected: [label], other: false }
      const all = answers.map((x, i) => (i === active ? next : x))
      setAnswers(all)
      advance(active, all)
    }
  }

  const toggleOther = () => {
    const other = !a.other
    update(active, { ...a, other, selected: q.multi_select || !other ? a.selected : [] })
    if (other) setTimeout(() => otherRef.current?.focus(), 0)
  }

  const submit = async () => {
    if (!done || sending) return
    setSending(true)
    try {
      await onSubmit(formatAnswers(questions, answers))
    } finally {
      setSending(false)
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return
      const n = Number(e.key)
      if (n >= 1 && n <= q.options.length) pick(q.options[n - 1].label)
      else if (n === q.options.length + 1) toggleOther()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  return (
    <div className="mb-2 overflow-hidden rounded-2xl bg-surface shadow-[0_0_0_1px_var(--ring),0_8px_24px_-12px_rgba(0,0,0,0.15)]">
      <div className="flex items-center gap-2 border-b border-hair px-4 py-2">
        <MessageCircleQuestion size={15} className="shrink-0 text-accent" />
        <div className="scroll-thin flex min-w-0 flex-1 gap-1 overflow-x-auto">
          {questions.length > 1
            ? questions.map((item, i) => (
                <button
                  key={i}
                  onClick={() => setActive(i)}
                  className={clsx(
                    'flex shrink-0 items-center gap-1 rounded-full px-2.5 py-0.5 text-xs transition',
                    i === active ? 'bg-accent-soft font-medium text-accent' : 'text-ink-2 hover:bg-surface-2',
                  )}
                >
                  {answered(answers[i]) ? <Check size={11} className="text-good" /> : null}
                  {item.header || `问题 ${i + 1}`}
                </button>
              ))
            : <span className="text-xs font-medium text-ink-2">{q.header || '需要你确认'}</span>}
        </div>
        <button onClick={onDismiss} className="shrink-0 rounded-md p-1 text-muted hover:bg-surface-2 hover:text-ink" title="不选了，直接输入">
          <X size={14} />
        </button>
      </div>

      <div className="px-4 pt-3 pb-1">
        <p className="mb-2.5 text-[15px] font-medium">
          {q.question}
          <span className="ml-2 text-xs font-normal text-muted">{q.multi_select ? '可多选' : '单选'}</span>
        </p>
        <div className="scroll-thin -mx-1 max-h-[42vh] space-y-1.5 overflow-y-auto p-1">
          {q.options.map((opt, i) => {
            const { text, recommended } = splitLabel(opt.label)
            const on = a.selected.includes(opt.label)
            return (
              <button
                key={opt.label}
                onClick={() => pick(opt.label)}
                className={clsx(
                  'flex w-full items-start gap-3 rounded-xl px-3 py-2 text-left transition',
                  on ? 'bg-accent-soft shadow-[0_0_0_1.5px_var(--accent)]' : 'shadow-[0_0_0_1px_var(--hair)] hover:bg-surface-2',
                )}
              >
                <Indicator multi={q.multi_select} on={on} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2 text-sm">
                    {text}
                    {recommended ? <span className="rounded bg-accent px-1.5 py-px text-[10px] font-medium text-accent-ink">推荐</span> : null}
                  </span>
                  {opt.description ? <span className="mt-0.5 block text-xs text-muted">{opt.description}</span> : null}
                </span>
                <kbd className="mt-0.5 hidden font-sans text-[11px] text-muted sm:block">{i + 1}</kbd>
              </button>
            )
          })}
          <div
            className={clsx(
              'flex items-center gap-3 rounded-xl px-3 py-2 transition',
              a.other ? 'bg-accent-soft shadow-[0_0_0_1.5px_var(--accent)]' : 'shadow-[0_0_0_1px_var(--hair)]',
            )}
          >
            <button onClick={toggleOther} className="flex shrink-0 items-center gap-3 text-sm" aria-label="其他">
              <Indicator multi={q.multi_select} on={a.other} />
              其他
            </button>
            <input
              ref={otherRef}
              value={a.otherText}
              onChange={(e) =>
                update(active, {
                  ...a,
                  otherText: e.target.value,
                  other: e.target.value.trim().length > 0 || a.other,
                  selected: q.multi_select || !e.target.value.trim() ? a.selected : [],
                })
              }
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  if (done) void submit()
                  else advance(active, answers)
                }
              }}
              placeholder="自己填写…"
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted"
            />
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 px-4 pt-1 pb-3">
        {questions.length > 1 ? (
          <>
            <button
              disabled={active === 0}
              onClick={() => setActive(active - 1)}
              className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-ink disabled:opacity-30"
              aria-label="上一题"
            >
              <ChevronLeft size={16} />
            </button>
            <span className="text-xs text-muted tabular-nums">
              {active + 1} / {questions.length}
            </span>
            <button
              disabled={last}
              onClick={() => setActive(active + 1)}
              className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-ink disabled:opacity-30"
              aria-label="下一题"
            >
              <ChevronRight size={16} />
            </button>
          </>
        ) : null}
        <span className="flex-1" />
        <button
          onClick={() => (done ? void submit() : setActive(answers.findIndex((x) => !answered(x))))}
          disabled={sending}
          className={clsx(
            'rounded-lg px-4 py-1.5 text-sm font-medium transition',
            done ? 'bg-accent text-accent-ink hover:opacity-90' : 'bg-surface-2 text-ink-2 hover:text-ink',
            'disabled:opacity-50',
          )}
        >
          {done ? (sending ? '发送中…' : '提交') : `还有 ${answers.filter((x) => !answered(x)).length} 题未选`}
        </button>
      </div>
    </div>
  )
}

function Indicator({ multi, on }: { multi: boolean; on: boolean }) {
  return (
    <span
      className={clsx(
        'mt-0.5 flex size-4 shrink-0 items-center justify-center transition',
        multi ? 'rounded-[5px]' : 'rounded-full',
        on ? 'bg-accent text-accent-ink' : 'shadow-[inset_0_0_0_1.5px_var(--muted)]',
      )}
    >
      {on ? multi ? <Check size={11} strokeWidth={3} /> : <span className="size-1.5 rounded-full bg-accent-ink" /> : null}
    </span>
  )
}
