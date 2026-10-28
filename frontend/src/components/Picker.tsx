import { ChevronDown } from 'lucide-react'
import type { ReactNode } from 'react'

/** 输入框上方的轻量选择器（数据源 / 模型） */
export function Picker({
  icon,
  value,
  options,
  onChange,
  placeholder,
  disabled,
}: {
  icon: ReactNode
  value: string | null
  options: { value: string; label: string; disabled?: boolean }[]
  onChange: (value: string | null) => void
  placeholder: string
  disabled?: boolean
}) {
  const current = options.find((o) => o.value === value)
  return (
    <label className="relative inline-flex max-w-[220px] items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-[13px] text-ink-2 shadow-[0_0_0_1px_var(--ring)] transition hover:text-ink">
      <span className="text-muted">{icon}</span>
      <span className="truncate">{current?.label ?? placeholder}</span>
      <ChevronDown size={13} className="shrink-0 text-muted" />
      <select
        className="absolute inset-0 cursor-pointer opacity-0"
        value={value ?? ''}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}
