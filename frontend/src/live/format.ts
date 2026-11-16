const relative = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

/** How long ago an instant was, in words: "now", "5 minutes ago", "2 hours ago". */
export function formatAgo(instant: number, now: number): string {
  const seconds = Math.round((instant - now) / 1000)
  if (Math.abs(seconds) < 60) return relative.format(0, 'second')
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) return relative.format(minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 48) return relative.format(hours, 'hour')
  return relative.format(Math.round(hours / 24), 'day')
}
