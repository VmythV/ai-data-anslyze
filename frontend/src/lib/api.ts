import type {
  Channel,
  ChannelInput,
  Conversation,
  ConversationDetail,
  DataSource,
  DataSourceInput,
  Domain,
  DomainInput,
  FederationStatus,
  InferResult,
  Relation,
  RelationInput,
  SchemaTable,
  TestResult,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** 登录失效时由 App 订阅，跳回登录页 */
export const authEvents = new EventTarget()

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: 'same-origin',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    if (res.status === 401 && path !== '/auth/login') authEvents.dispatchEvent(new Event('unauthorized'))
    let message = `请求失败（${res.status}）`
    try {
      const data = await res.json()
      if (typeof data.detail === 'string') message = data.detail
      else if (Array.isArray(data.detail)) message = data.detail.map((d: { msg: string }) => d.msg).join('；')
    } catch {
      /* 非 JSON 响应 */
    }
    throw new ApiError(res.status, message)
  }
  return res.json() as Promise<T>
}

export const api = {
  me: () => request<{ login_required: boolean; authenticated: boolean }>('GET', '/auth/me'),
  login: (password: string) => request<{ ok: boolean }>('POST', '/auth/login', { password }),
  logout: () => request<{ ok: boolean }>('POST', '/auth/logout'),

  channels: () => request<Channel[]>('GET', '/channels'),
  createChannel: (body: ChannelInput) => request<Channel>('POST', '/channels', body),
  updateChannel: (id: string, body: ChannelInput) => request<Channel>('PUT', `/channels/${id}`, body),
  deleteChannel: (id: string) => request<{ ok: boolean }>('DELETE', `/channels/${id}`),
  testChannel: (id: string) => request<TestResult>('POST', `/channels/${id}/test`),

  datasources: () => request<DataSource[]>('GET', '/datasources'),
  createDataSource: (body: DataSourceInput) => request<DataSource>('POST', '/datasources', body),
  updateDataSource: (id: string, body: DataSourceInput) => request<DataSource>('PUT', `/datasources/${id}`, body),
  deleteDataSource: (id: string) => request<{ ok: boolean }>('DELETE', `/datasources/${id}`),
  testDataSource: (body: DataSourceInput & { id?: string }) => request<TestResult>('POST', '/datasources/test', body),
  datasourceSchema: (id: string) => request<SchemaTable[]>('GET', `/datasources/${id}/schema`),
  refreshSchema: (id: string) => request<TestResult>('POST', `/datasources/${id}/refresh-schema`),

  domains: () => request<Domain[]>('GET', '/domains'),
  createDomain: (body: DomainInput) => request<Domain>('POST', '/domains', body),
  updateDomain: (id: string, body: DomainInput) => request<Domain>('PUT', `/domains/${id}`, body),
  deleteDomain: (id: string) => request<{ ok: boolean }>('DELETE', `/domains/${id}`),

  relations: () => request<Relation[]>('GET', '/relations'),
  createRelation: (body: RelationInput) => request<Relation>('POST', '/relations', body),
  updateRelation: (id: string, body: RelationInput) => request<Relation>('PUT', `/relations/${id}`, body),
  confirmRelation: (id: string) => request<Relation>('POST', `/relations/${id}/confirm`),
  inferRelations: (datasourceIds: string[] = []) =>
    request<InferResult>('POST', '/relations/infer', { datasource_ids: datasourceIds }),
  federation: () => request<FederationStatus>('GET', '/federation'),
  deleteRelation: (id: string) => request<{ ok: boolean }>('DELETE', `/relations/${id}`),

  conversations: () => request<Conversation[]>('GET', '/conversations'),
  createConversation: (body: { channel_id?: string | null; datasource_ids?: string[]; domain_id?: string | null }) =>
    request<Conversation>('POST', '/conversations', body),
  conversation: (id: string) => request<ConversationDetail>('GET', `/conversations/${id}`),
  patchConversation: (
    id: string,
    body: Partial<Pick<Conversation, 'title' | 'channel_id' | 'datasource_ids' | 'domain_id'>>,
  ) =>
    request<Conversation>('PATCH', `/conversations/${id}`, body),
  deleteConversation: (id: string) => request<{ ok: boolean }>('DELETE', `/conversations/${id}`),
  sendMessage: (id: string, content: string) =>
    request<{ run_id: string; message_id: string }>('POST', `/conversations/${id}/messages`, { content }),
  stop: (id: string) => request<{ stopped: boolean }>('POST', `/conversations/${id}/stop`),

  chartTheme: () => request<{ light: object; dark: object }>('GET', '/chart-theme'),
}

export const reportUrl = (id: string, download = false) =>
  `/api/artifacts/${id}/report.html${download ? '?download=true' : ''}`
