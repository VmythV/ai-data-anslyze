import { Download } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { clsx } from 'clsx'
import { echarts, loadChartThemes, useColorScheme } from '../lib/echarts'

export function ChartCard({ title, option, compact }: { title: string; option: Record<string, unknown>; compact?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const scheme = useColorScheme()
  const [error, setError] = useState('')

  useEffect(() => {
    let disposed = false
    let observer: ResizeObserver | null = null
    void loadChartThemes().then(() => {
      if (disposed || !ref.current) return
      const chart = echarts.init(ref.current, scheme === 'dark' ? 'da-dark' : 'da-light', { renderer: 'canvas' })
      chartRef.current = chart
      try {
        chart.setOption(option)
        setError('')
      } catch (err) {
        setError(`图表配置有误：${(err as Error).message}`)
      }
      observer = new ResizeObserver(() => chart.resize())
      observer.observe(ref.current)
    })
    return () => {
      disposed = true
      observer?.disconnect()
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [option, scheme])

  const download = () => {
    const chart = chartRef.current
    if (!chart) return
    const url = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: scheme === 'dark' ? '#1a1a19' : '#ffffff' })
    const a = document.createElement('a')
    a.href = url
    a.download = `${title}.png`
    a.click()
  }

  return (
    <div className="flex h-full flex-col rounded-2xl bg-surface p-4 shadow-[0_0_0_1px_var(--ring)] sm:p-5">
      <div className="mb-2 flex items-start justify-between gap-3">
        {/* 两列排布时标题统一占两行高度，长标题省略，保证同一行的图形区上下对齐 */}
        <h3
          title={title}
          className={clsx(
            'font-semibold leading-snug',
            compact ? 'line-clamp-2 min-h-[2.75em] text-[14px]' : 'text-[15px]',
          )}
        >
          {title}
        </h3>
        <button
          onClick={download}
          className="shrink-0 rounded-md p-1.5 text-muted transition hover:bg-surface-2 hover:text-ink"
          title="下载图片"
        >
          <Download size={15} />
        </button>
      </div>
      {error ? <p className="py-6 text-sm text-bad">{error}</p> : null}
      <div ref={ref} className={clsx('mt-auto w-full', compact ? 'h-[300px]' : 'h-[340px]')} style={{ display: error ? 'none' : undefined }} />
    </div>
  )
}
