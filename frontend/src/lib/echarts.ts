import * as echarts from 'echarts'
import { useEffect, useState } from 'react'
import { api } from './api'

let themesLoaded: Promise<void> | null = null

/** 主题由后端下发：聊天卡片与 HTML 报告用同一份 */
export function loadChartThemes(): Promise<void> {
  themesLoaded ??= api
    .chartTheme()
    .then((themes) => {
      echarts.registerTheme('da-light', themes.light)
      echarts.registerTheme('da-dark', themes.dark)
    })
    .catch(() => {
      themesLoaded = null
    })
  return themesLoaded ?? Promise.resolve()
}

const media = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null

export function useColorScheme(): 'light' | 'dark' {
  const [dark, setDark] = useState(media?.matches ?? false)
  useEffect(() => {
    if (!media) return
    const onChange = () => setDark(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return dark ? 'dark' : 'light'
}

export { echarts }
