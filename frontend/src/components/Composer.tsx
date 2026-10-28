import { ArrowUp, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { clsx } from 'clsx'

export function Composer({
  onSend,
  onStop,
  running,
  disabled,
  placeholder = '问一个关于数据的问题，例如：今年各大区的销售额对比如何？',
  autoFocus,
}: {
  onSend: (text: string) => Promise<void> | void
  onStop?: () => void
  running: boolean
  disabled?: boolean
  placeholder?: string
  autoFocus?: boolean
}) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`
  }, [text])

  const submit = async () => {
    const value = text.trim()
    if (!value || running || disabled || sending) return
    setSending(true)
    try {
      setText('')
      await onSend(value)
    } catch {
      setText(value)
    } finally {
      setSending(false)
      ref.current?.focus()
    }
  }

  return (
    <div className="rounded-2xl bg-surface p-2 shadow-[0_0_0_1px_var(--ring),0_8px_24px_-12px_rgba(0,0,0,0.15)] transition focus-within:shadow-[0_0_0_1.5px_var(--accent),0_8px_24px_-12px_rgba(0,0,0,0.15)]">
      <textarea
        ref={ref}
        rows={1}
        value={text}
        autoFocus={autoFocus}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            void submit()
          }
        }}
        className="scroll-thin block max-h-[220px] w-full resize-none bg-transparent px-2.5 py-2 text-[15px] leading-relaxed outline-none placeholder:text-muted disabled:opacity-50"
      />
      <div className="flex items-center justify-between px-1.5 pb-0.5">
        <span className="text-[11px] text-muted">Enter 发送 · Shift + Enter 换行</span>
        {running ? (
          <button
            onClick={onStop}
            className="flex size-8 items-center justify-center rounded-full bg-ink text-page transition hover:opacity-80"
            title="停止"
          >
            <Square size={12} fill="currentColor" />
          </button>
        ) : (
          <button
            onClick={() => void submit()}
            disabled={!text.trim() || disabled || sending}
            className={clsx(
              'flex size-8 items-center justify-center rounded-full transition',
              text.trim() && !disabled ? 'bg-accent text-accent-ink hover:opacity-90' : 'bg-surface-2 text-muted',
            )}
            title="发送"
          >
            <ArrowUp size={16} strokeWidth={2.4} />
          </button>
        )}
      </div>
    </div>
  )
}
