import type { ReactNode } from 'react'

/** A setting's or a command's own name, as the service and its command line take it. */
export function Code({ children }: { children: string }) {
  return (
    <code className="data inline-block max-w-full rounded-compact bg-canvas-mist px-sm py-[3px] font-mono text-[0.8125rem] text-primary-ink [overflow-wrap:anywhere]">
      {children}
    </code>
  )
}

/** Terms and their values: Label terms in secondary ink beside Body values, stacked on a phone. */
export function Facts({ children }: { children: ReactNode }) {
  return (
    <dl className="grid max-w-[70ch] items-baseline gap-y-xs text-body roomy:grid-cols-[minmax(10rem,auto)_1fr] roomy:gap-x-lg roomy:gap-y-sm">
      {children}
    </dl>
  )
}

export function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-label text-secondary-ink">{term}</dt>
      <dd className="mb-sm flex min-w-0 flex-col items-start gap-xs text-primary-ink roomy:mb-0">{children}</dd>
    </div>
  )
}
