import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LogOut, MessageSquarePlus, Settings, Trash2 } from 'lucide-react'
import { NavLink, useNavigate, useParams } from 'react-router-dom'
import { clsx } from 'clsx'
import { api } from '../lib/api'
import type { Conversation } from '../lib/types'

function groupLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const days = Math.floor((new Date(today.toDateString()).getTime() - new Date(d.toDateString()).getTime()) / 86400000)
  if (days <= 0) return '今天'
  if (days === 1) return '昨天'
  if (days < 7) return '最近 7 天'
  if (days < 30) return '最近 30 天'
  return '更早'
}

export function Sidebar({ loginRequired, onNavigate }: { loginRequired: boolean; onNavigate?: () => void }) {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['conversations'], queryFn: api.conversations })
  const remove = useMutation({
    mutationFn: api.deleteConversation,
    onSuccess: (_, removed) => {
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      if (removed === id) navigate('/')
    },
  })

  const groups: [string, Conversation[]][] = []
  for (const c of data) {
    const label = groupLabel(c.updated_at)
    const last = groups[groups.length - 1]
    if (last && last[0] === label) last[1].push(c)
    else groups.push([label, [c]])
  }

  return (
    <aside className="flex h-full w-full flex-col bg-sidebar">
      <div className="flex items-center gap-2.5 px-4 pt-4 pb-3">
        <img src="/favicon.svg" alt="" className="size-7" />
        <span className="text-[15px] font-semibold tracking-tight">AI 数据分析</span>
      </div>
      <div className="px-3 pb-2">
        <NavLink
          to="/"
          onClick={onNavigate}
          className="flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium shadow-[0_0_0_1px_var(--ring)] transition hover:bg-surface-2"
        >
          <MessageSquarePlus size={16} className="text-accent" />
          新建分析
        </NavLink>
      </div>
      <nav className="scroll-thin flex-1 overflow-y-auto px-2 pb-2">
        {groups.map(([label, items]) => (
          <div key={label} className="mt-3">
            <div className="px-2 pb-1 text-[11px] font-medium text-muted">{label}</div>
            {items.map((c) => (
              <NavLink
                key={c.id}
                to={`/c/${c.id}`}
                onClick={onNavigate}
                className={({ isActive }) =>
                  clsx(
                    'group flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13px] transition',
                    isActive ? 'bg-surface font-medium text-ink shadow-[0_0_0_1px_var(--ring)]' : 'text-ink-2 hover:bg-surface/70',
                  )
                }
              >
                <span className="min-w-0 flex-1 truncate">{c.title}</span>
                <button
                  onClick={(e) => {
                    e.preventDefault()
                    if (confirm(`删除会话「${c.title}」？`)) remove.mutate(c.id)
                  }}
                  className="rounded p-1 text-muted opacity-0 transition group-hover:opacity-100 hover:text-bad"
                  title="删除"
                >
                  <Trash2 size={13} />
                </button>
              </NavLink>
            ))}
          </div>
        ))}
        {data.length === 0 ? <p className="px-3 pt-6 text-center text-xs text-muted">还没有会话</p> : null}
      </nav>
      <div className="flex items-center gap-1 border-t border-hair px-2 py-2">
        <NavLink
          to="/settings"
          onClick={onNavigate}
          className={({ isActive }) =>
            clsx('flex flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] transition hover:bg-surface/70', isActive ? 'text-ink' : 'text-ink-2')
          }
        >
          <Settings size={15} />
          设置
        </NavLink>
        {loginRequired ? (
          <button
            onClick={async () => {
              await api.logout()
              location.reload()
            }}
            className="rounded-lg p-2 text-muted transition hover:bg-surface/70 hover:text-ink"
            title="退出登录"
          >
            <LogOut size={15} />
          </button>
        ) : null}
      </div>
    </aside>
  )
}
