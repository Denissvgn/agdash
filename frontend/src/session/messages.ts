/**
 * Whether a prompt is a message between agents whose text the harness encrypted, so only
 * its header (the task and the sender) could be kept. Events stored before this was
 * noted are told by their shape: such a header ends at its bare "Payload:" line.
 */
export function messageEncrypted(payload: Record<string, unknown>): boolean {
  const extra = (payload.extra ?? {}) as Record<string, unknown>
  if (extra.encrypted === true) return true
  const text = typeof payload.text === 'string' ? payload.text : ''
  return extra.from_agent === true && /(^|\n)Payload:\s*$/.test(text)
}
