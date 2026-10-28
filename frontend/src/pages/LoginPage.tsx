import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../lib/api'

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  return (
    <div className="flex h-full items-center justify-center px-4">
      <form
        className="w-full max-w-sm rounded-2xl bg-surface p-7 shadow-[0_0_0_1px_var(--ring),0_12px_32px_-16px_rgba(0,0,0,0.2)]"
        onSubmit={async (e) => {
          e.preventDefault()
          setLoading(true)
          setError('')
          try {
            await api.login(password)
            onLogin()
          } catch (err) {
            setError((err as Error).message)
          } finally {
            setLoading(false)
          }
        }}
      >
        <img src="/favicon.svg" alt="" className="mb-5 size-10" />
        <h1 className="text-xl font-semibold tracking-tight">AI 数据分析</h1>
        <p className="mt-1 mb-6 text-sm text-muted">请输入访问口令</p>
        <input
          type="password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-lg bg-page px-3 py-2.5 text-sm shadow-[0_0_0_1px_var(--hair)] outline-none focus:shadow-[0_0_0_1.5px_var(--accent)]"
          placeholder="访问口令"
        />
        {error ? <p className="mt-2 text-sm text-bad">{error}</p> : null}
        <button disabled={loading || !password} className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-accent py-2.5 text-sm font-medium text-accent-ink hover:opacity-90 disabled:opacity-60">
          {loading ? <Loader2 size={15} className="animate-spin" /> : null}进入
        </button>
      </form>
    </div>
  )
}
