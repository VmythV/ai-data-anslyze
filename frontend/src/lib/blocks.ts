/** 运行事件 → 消息块。与后端 runs/blocks.py 同一套规则：后端落库，这里实时渲染。 */
import type { Block, RunEvent } from './types'

export function applyEvent(blocks: Block[], event: RunEvent): Block[] {
  const next = blocks.slice()
  const last = next[next.length - 1]
  switch (event.type) {
    case 'text_start':
      if (!last || last.kind !== 'text' || last.text) next.push({ kind: 'text', text: '' })
      return next
    case 'text':
      if (last && last.kind === 'text') next[next.length - 1] = { ...last, text: last.text + event.delta }
      else next.push({ kind: 'text', text: event.delta })
      return next
    case 'tool_call':
      next.push({ kind: 'tool', id: event.id, name: event.name, input: event.input ?? {}, status: 'running', output: '' })
      return next
    case 'tool_result': {
      for (let i = next.length - 1; i >= 0; i--) {
        const block = next[i]
        if (block.kind === 'tool' && block.id === event.id) {
          next[i] = { ...block, status: event.is_error ? 'error' : 'done', output: event.output }
          break
        }
      }
      return next
    }
    case 'table':
    case 'chart':
    case 'kpis':
    case 'report':
    case 'question':
    case 'relation':
    case 'error': {
      const { type, seq: _seq, ...rest } = event
      next.push({ kind: type, ...rest } as Block)
      return next
    }
    default:
      return blocks
  }
}

/** 渲染分组：连续的工具调用折叠成一组「分析步骤」，其余块各自成段 */
export type Segment =
  | { kind: 'steps'; tools: Extract<Block, { kind: 'tool' }>[] }
  | { kind: 'charts'; charts: Extract<Block, { kind: 'chart' }>[] }
  | { kind: 'block'; block: Exclude<Block, { kind: 'tool' | 'chart' }> }

const VISUAL_TOOLS = new Set(['render_chart', 'render_kpis'])

/** 这条回答里有没有图表或指标卡：有的话原始查询结果默认折叠，把版面让给图 */
export function hasVisual(blocks: Block[]): boolean {
  return blocks.some((b) => b.kind === 'chart' || b.kind === 'kpis')
}

export function segment(blocks: Block[]): Segment[] {
  const out: Segment[] = []
  for (const block of blocks) {
    // 这些工具的结果本身就以问题 / 图表 / 指标卡展示，成功时不再单列一个步骤（出错时仍然显示）
    if (block.kind === 'tool' && (block.name === 'ask_user' || (VISUAL_TOOLS.has(block.name) && block.status === 'done'))) continue
    if (block.kind === 'tool') {
      const last = out[out.length - 1]
      if (last && last.kind === 'steps') last.tools.push(block)
      else out.push({ kind: 'steps', tools: [block] })
    } else if (block.kind === 'chart') {
      const last = out[out.length - 1]
      if (last && last.kind === 'charts') last.charts.push(block)
      else out.push({ kind: 'charts', charts: [block] })
    } else if (!(block.kind === 'text' && !block.text.trim())) {
      out.push({ kind: 'block', block })
    }
  }
  return out
}
