import { useSearchParams } from 'react-router-dom'
import { clsx } from 'clsx'
import { ChannelsTab } from './ChannelsTab'
import { DataSourcesTab } from './DataSourcesTab'
import { DomainsTab } from './DomainsTab'
import { RelationsTab } from './RelationsTab'

const TABS = [
  { key: 'channels', label: '模型渠道', view: ChannelsTab },
  { key: 'datasources', label: '数据源', view: DataSourcesTab },
  { key: 'domains', label: '数据域', view: DomainsTab },
  { key: 'relations', label: '关联关系', view: RelationsTab },
] as const

export function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const active = TABS.find((t) => t.key === params.get('tab')) ?? TABS[0]
  const View = active.view
  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className="mx-auto max-w-[800px] px-4 pt-16 pb-10 sm:px-6 md:pt-8">
        <h1 className="mb-5 text-xl font-semibold tracking-tight">设置</h1>
        <div className="scroll-thin mb-7 flex gap-1 overflow-x-auto border-b border-hair">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setParams({ tab: t.key }, { replace: true })}
              className={clsx(
                '-mb-px shrink-0 border-b-2 px-3 py-2 text-sm transition',
                t.key === active.key ? 'border-accent font-medium text-ink' : 'border-transparent text-muted hover:text-ink',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        <View />
      </div>
    </div>
  )
}
