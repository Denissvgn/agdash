import { useId } from 'react'

export const CHOICE_PILL =
  'toggle-chip relative cursor-pointer has-[:checked]:border-planning-thread has-[:checked]:bg-planning-thread-soft has-[:checked]:text-planning-thread has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-planning-thread'

/**
 * One of a few choices, as pills over native radio buttons, with its label beside the
 * pills: how a page is grouped, measured or ranged. The page keeps the choice in its
 * address. A labelled radio group rather than a fieldset, whose legend would take a
 * line of its own.
 */
export function Choice<T extends string>({
  legend,
  value,
  options,
  label,
  onChange,
}: {
  legend: string
  value: T
  options: readonly T[]
  label: (option: T) => string
  onChange: (option: T) => void
}) {
  const name = useId()
  return (
    <div aria-labelledby={`${name}-legend`} className="flex flex-wrap items-center gap-sm" role="radiogroup">
      <span className="text-label text-secondary-ink" id={`${name}-legend`}>
        {legend}
      </span>
      {options.map((option) => (
        <label key={option} className={CHOICE_PILL}>
          <input checked={value === option} className="sr-only" name={name} type="radio" value={option} onChange={() => onChange(option)} />
          {label(option)}
        </label>
      ))}
    </div>
  )
}
