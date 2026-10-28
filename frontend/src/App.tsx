import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Menu } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Outlet, Route, Routes } from 'react-router-dom'
import { clsx } from 'clsx'
import { Sidebar } from './components/Sidebar'
import { api, authEvents } from './lib/api'
import { ChatPage, NewChatPage } from './pages/ChatPage'
import { LoginPage } from './pages/LoginPage'
import { SettingsPage } from './pages/settings/SettingsPage'

function Layout({ loginRequired }: { loginRequired: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="flex h-full">
      <div
        className={clsx(
          'fixed inset-y-0 left-0 z-40 w-[264px] shrink-0 transition-transform md:static md:translate-x-0',
          open ? 'translate-x-0 shadow-2xl' : '-translate-x-full',
        )}
      >
        <Sidebar loginRequired={loginRequired} onNavigate={() => setOpen(false)} />
      </div>
      {open ? <div className="fixed inset-0 z-30 bg-black/30 md:hidden" onClick={() => setOpen(false)} /> : null}
      <main className="relative flex min-w-0 flex-1 flex-col">
        <button
          onClick={() => setOpen(true)}
          className="absolute top-3 left-3 z-20 rounded-lg bg-surface p-2 text-ink-2 shadow-[0_0_0_1px_var(--ring)] md:hidden"
          aria-label="打开菜单"
        >
          <Menu size={16} />
        </button>
        <Outlet />
      </main>
    </div>
  )
}

export default function App() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: api.me, retry: false })

  useEffect(() => {
    const onUnauthorized = () => void queryClient.invalidateQueries({ queryKey: ['me'] })
    authEvents.addEventListener('unauthorized', onUnauthorized)
    return () => authEvents.removeEventListener('unauthorized', onUnauthorized)
  }, [queryClient])

  if (me.isLoading) return null
  if (me.error) return <div className="flex h-full items-center justify-center text-sm text-muted">无法连接服务端，请稍后刷新</div>
  if (me.data && !me.data.authenticated) {
    return <LoginPage onLogin={() => void queryClient.invalidateQueries()} />
  }

  return (
    <Routes>
      <Route element={<Layout loginRequired={!!me.data?.login_required} />}>
        <Route index element={<NewChatPage />} />
        <Route path="c/:id" element={<ChatPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  )
}
