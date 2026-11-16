import { useEffect, useRef, useState } from 'react'
import { localToken } from '../api/client'

/**
 * The service's live stream, read with fetch: the stream needs the local token in an
 * Authorization header, which the browser's EventSource cannot send.
 */

export type StreamStatus = 'connecting' | 'live' | 'retrying'

export interface StreamMessage {
  event: string
  data: unknown
}

/** Splits server-sent-event text into messages; returns those complete and the rest. */
export function parseEvents(buffer: string): [StreamMessage[], string] {
  const messages: StreamMessage[] = []
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n')
  const rest = blocks.pop() ?? ''
  for (const block of blocks) {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue
      const colon = line.indexOf(':')
      const field = colon === -1 ? line : line.slice(0, colon)
      const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '')
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
    }
    if (!data.length) continue
    try {
      messages.push({ event, data: JSON.parse(data.join('\n')) })
    } catch {
      // A malformed message is skipped; the next refetch brings the truth anyway.
    }
  }
  return [messages, rest]
}

const FIRST_RETRY_MS = 1000
const LAST_RETRY_MS = 30_000
/** The service writes at least every 15 seconds; this much silence means the stream is gone. */
export const SILENCE_MS = 40_000
const WATCH_MS = 5000

/** Follows the live stream while mounted, reconnecting with growing pauses. */
export function useLiveStream(onMessage: (message: StreamMessage) => void): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const handler = useRef(onMessage)
  useEffect(() => {
    handler.current = onMessage
  })

  useEffect(() => {
    const controller = new AbortController()
    let pause = FIRST_RETRY_MS
    let timer: ReturnType<typeof setTimeout> | undefined

    const connect = async () => {
      const config = window.__AGDASH__ ?? {}
      const headers: Record<string, string> = { Accept: 'text/event-stream' }
      const token = localToken()
      if (token) headers.Authorization = `Bearer ${token}`
      // One connection; a proxy or a sleeping machine can leave it open but dead, so
      // silence past the service's keep-alive ends it.
      const attempt = new AbortController()
      const stop = () => attempt.abort()
      controller.signal.addEventListener('abort', stop)
      let heard = Date.now()
      const watch = setInterval(() => {
        if (Date.now() - heard > SILENCE_MS) attempt.abort()
      }, WATCH_MS)
      try {
        const response = await fetch(`${config.apiBase ?? ''}/api/stream`, {
          headers,
          signal: attempt.signal,
        })
        if (!response.ok || !response.body) throw new Error(`stream ${response.status}`)
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          heard = Date.now()
          buffer += decoder.decode(value, { stream: true })
          const [messages, rest] = parseEvents(buffer)
          buffer = rest
          for (const message of messages) {
            if (message.event === 'hello') {
              setStatus('live')
              pause = FIRST_RETRY_MS
            }
            handler.current(message)
          }
        }
        throw new Error('stream closed')
      } catch {
        if (controller.signal.aborted) return
        setStatus('retrying')
        timer = setTimeout(connect, pause)
        pause = Math.min(pause * 2, LAST_RETRY_MS)
      } finally {
        clearInterval(watch)
        controller.signal.removeEventListener('abort', stop)
      }
    }

    void connect()
    return () => {
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [])

  return status
}
