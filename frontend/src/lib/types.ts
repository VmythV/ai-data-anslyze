/** 与后端 api/schemas.py、runs/blocks.py 对应 */

export type Protocol = 'anthropic' | 'openai'

export interface Channel {
  id: string
  name: string
  protocol: Protocol
  base_url: string
  api_key_masked: string
  model: string
  is_default: boolean
  supported: boolean
}

export interface ChannelInput {
  name: string
  protocol: Protocol
  base_url: string
  api_key: string
  model: string
  is_default: boolean
}

export type DataSourceKind = 'mysql' | 'postgresql' | 'clickhouse' | 'mongodb'

export interface DataSource {
  id: string
  name: string
  alias: string
  kind: DataSourceKind
  host: string
  port: number
  username: string
  database: string
  options: Record<string, string | boolean>
  description: string
  schema_refreshed_at: string | null
  schema_error: string | null
}

export interface DataSourceInput extends Omit<DataSource, 'id' | 'schema_refreshed_at' | 'schema_error'> {
  password: string
}

export interface Domain {
  id: string
  name: string
  description: string
  datasource_ids: string[]
}

export type DomainInput = Omit<Domain, 'id'>

export type Cardinality = '1:1' | '1:n' | 'n:1' | 'n:n'

export interface RelationInput {
  left_datasource_id: string
  left_table: string
  left_column: string
  right_datasource_id: string
  right_table: string
  right_column: string
  cardinality: Cardinality
  transform: string
  description: string
}

export interface Relation extends RelationInput {
  id: string
  source: 'manual' | 'ai'
  status: 'confirmed' | 'pending'
  match_rate: number | null
  created_at: string
}

export interface SchemaTable {
  name: string
  comment: string
  row_estimate: number | null
  columns: [string, string, string][]
}

export interface Conversation {
  id: string
  title: string
  channel_id: string | null
  datasource_ids: string[]
  domain_id: string | null
  created_at: string
  updated_at: string
}

export interface QuestionOption {
  label: string
  description: string
}

export interface Question {
  question: string
  header: string
  multi_select: boolean
  options: QuestionOption[]
}

export interface Kpi {
  label: string
  value: string
  delta: string
  tone: 'good' | 'bad' | 'neutral'
  note: string
}

export type ToolStatus = 'running' | 'done' | 'error' | 'interrupted'

export type Block =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; id: string; name: string; input: Record<string, unknown>; status: ToolStatus; output: string }
  | {
      kind: 'table'
      title: string
      datasource?: string
      language?: 'sql' | 'mongo'
      sql: string
      columns: string[]
      rows: unknown[][]
      total_rows: number
      truncated: boolean
      file: string
    }
  | { kind: 'chart'; artifact_id: string; title: string; option: Record<string, unknown> }
  | { kind: 'kpis'; title: string; items: Kpi[] }
  | { kind: 'report'; artifact_id: string; title: string }
  | { kind: 'question'; id: string; questions: Question[] }
  | { kind: 'relation'; left: string; right: string; match_rate: number | null; status: 'pending' | 'confirmed' }
  | { kind: 'error'; message: string }

export type MessageStatus = 'completed' | 'interrupted' | 'error' | 'running'

export interface Usage {
  duration_ms: number
  num_turns: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cost_usd: number | null
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  blocks: Block[]
  status: MessageStatus
  usage: Usage | null
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
  running: boolean
}

export type RunEvent = { seq: number } & (
  | { type: 'run_started'; run_id: string; message_id: string }
  | { type: 'text_start' }
  | { type: 'text'; delta: string }
  | { type: 'tool_call'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; id: string; is_error: boolean; output: string }
  | ({ type: 'table' } & Omit<Extract<Block, { kind: 'table' }>, 'kind'>)
  | ({ type: 'chart' } & Omit<Extract<Block, { kind: 'chart' }>, 'kind'>)
  | ({ type: 'kpis' } & Omit<Extract<Block, { kind: 'kpis' }>, 'kind'>)
  | ({ type: 'report' } & Omit<Extract<Block, { kind: 'report' }>, 'kind'>)
  | ({ type: 'question' } & Omit<Extract<Block, { kind: 'question' }>, 'kind'>)
  | ({ type: 'relation' } & Omit<Extract<Block, { kind: 'relation' }>, 'kind'>)
  | { type: 'error'; message: string }
  | { type: 'status'; message: string }
  | { type: 'done'; status: MessageStatus; message_id: string }
)

export interface TestResult {
  ok: boolean
  message: string
}

export interface FederationStatus {
  enabled: boolean
  catalogs: string[]
  error: string | null
}

export interface InferResult {
  evaluated: number
  created: number
  candidates: { left: string; right: string; reason: string; match_rate: number | null; sampled: number; error: string }[]
}
