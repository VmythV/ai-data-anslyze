import { Download, ExternalLink, FileText, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { reportUrl } from '../lib/api'

export function ReportCard({ id, title }: { id: string; title: string }) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <>
      <div className="flex items-center gap-3 rounded-2xl bg-surface p-4 shadow-[0_0_0_1px_var(--ring)]">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
          <FileText size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[15px] font-semibold">{title}</div>
          <div className="text-xs text-muted">HTML 分析报告 · 单文件，可离线打开</div>
        </div>
        <button
          onClick={() => setOpen(true)}
          className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-ink transition hover:opacity-90"
        >
          预览
        </button>
        <a
          href={reportUrl(id, true)}
          className="rounded-lg p-2 text-muted transition hover:bg-surface-2 hover:text-ink"
          title="下载 HTML"
        >
          <Download size={16} />
        </a>
      </div>
      {open ? (
        <div className="fixed inset-0 z-50 flex flex-col bg-black/40 p-0 backdrop-blur-sm sm:p-6" onClick={() => setOpen(false)}>
          <div
            className="mx-auto flex h-full w-full max-w-6xl flex-col overflow-hidden bg-page shadow-2xl sm:rounded-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 border-b border-hair bg-surface px-4 py-2.5">
              <FileText size={16} className="text-accent" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{title}</span>
              <a href={reportUrl(id)} target="_blank" rel="noreferrer" className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-ink" title="新窗口打开">
                <ExternalLink size={16} />
              </a>
              <a href={reportUrl(id, true)} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-ink" title="下载 HTML">
                <Download size={16} />
              </a>
              <button onClick={() => setOpen(false)} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-ink" title="关闭">
                <X size={16} />
              </button>
            </div>
            {/* 报告是 AI 生成的页面：只允许它运行自己的脚本，拿不到本站的 Cookie 和接口 */}
            <iframe src={reportUrl(id)} sandbox="allow-scripts allow-popups" className="w-full flex-1 border-0 bg-page" title={title} />
          </div>
        </div>
      ) : null}
    </>
  )
}
