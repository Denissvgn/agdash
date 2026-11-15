const when = new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' })

/** An instant in the reader's time zone, or null when the harness did not record one. */
export function formatWhen(value: string | null): string | null {
  return value ? when.format(new Date(value)) : null
}
