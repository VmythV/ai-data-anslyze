import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { applyEvent } from '../lib/blocks'
import type { Block, Message, RunEvent } from '../lib/types'

interface LiveRun {
  messageId: string | null
  blocks: Block[]
  status: string
  /** 已发出、还没出现在会话详情里的用户消息 */
  pendingUser: string | null
  done: boolean
}

const EMPTY: LiveRun = { messageId: null, blocks: [], status: '', pendingUser: null, done: false }

/**
 * 会话详情 + 进行中的运行。运行事件走 SSE，断线由 EventSource 带 Last-Event-ID 自动续上；
 * 运行结束后重新拉取详情，落库的消息替换掉实时渲染的那一份。
 */
export function useConversation(conversationId: string | undefined) {
  const queryClient = useQueryClient()
  const detail = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => api.conversation(conversationId!),
    enabled: !!conversationId,
  })
  const [live, setLive] = useState<LiveRun>(EMPTY)
  const sourceRef = useRef<EventSource | null>(null)

  const close = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
  }, [])

  const refresh = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] }),
      queryClient.invalidateQueries({ queryKey: ['conversations'] }),
    ])
  }, [queryClient, conversationId])

  const subscribe = useCallback(() => {
    if (!conversationId || sourceRef.current) return
    const source = new EventSource(`/api/conversations/${conversationId}/events`)
    sourceRef.current = source
    source.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as RunEvent
      setLive((prev) => {
        if (event.type === 'run_started') return { ...prev, messageId: event.message_id, blocks: [], done: false }
        if (event.type === 'status') return { ...prev, status: event.message }
        if (event.type === 'done') return { ...prev, done: true, status: '' }
        return { ...prev, blocks: applyEvent(prev.blocks, event), status: '' }
      })
      if (event.type === 'done') {
        close()
        void refresh()
      }
    }
    source.onerror = () => {
      // 服务端返回非 200（运行已结束并被清理）时浏览器不会再重连
      if (source.readyState === EventSource.CLOSED) {
        close()
        setLive((prev) => ({ ...prev, done: true }))
        void refresh()
      }
    }
  }, [conversationId, close, refresh])

  // 切换会话：断开旧的事件流
  useEffect(() => {
    setLive(EMPTY)
    return close
  }, [conversationId, close])

  // 打开一个正在运行的会话（刷新页面、从别的会话切回来）
  useEffect(() => {
    if (detail.data?.running && !sourceRef.current) subscribe()
  }, [detail.data?.running, subscribe])

  const messages: Message[] = detail.data?.messages ?? []
  const persisted = live.messageId != null && messages.some((m) => m.id === live.messageId)
  // 落库的版本到了，实时版本就退场
  useEffect(() => {
    if (persisted && live.done) setLive(EMPTY)
  }, [persisted, live.done])

  const send = useCallback(
    async (content: string, targetId = conversationId) => {
      if (!targetId) return
      setLive({ ...EMPTY, pendingUser: content })
      try {
        await api.sendMessage(targetId, content)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['conversation', targetId] }),
          queryClient.invalidateQueries({ queryKey: ['conversations'] }),
        ])
        setLive((prev) => ({ ...prev, pendingUser: null }))
        subscribe()
      } catch (err) {
        setLive(EMPTY)
        throw err
      }
    },
    [conversationId, queryClient, subscribe],
  )

  const stop = useCallback(async () => {
    if (conversationId) await api.stop(conversationId)
  }, [conversationId])

  const running =
    live.pendingUser != null || (live.messageId != null && !live.done) || (!!detail.data?.running && !live.done)
  const showLive = !persisted && (live.messageId != null || live.pendingUser != null)

  return {
    detail: detail.data,
    loading: detail.isLoading,
    error: detail.error,
    messages,
    live: showLive ? live : null,
    running,
    send,
    stop,
  }
}
