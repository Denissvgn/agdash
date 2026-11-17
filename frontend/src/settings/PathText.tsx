import { Fragment } from 'react'

/**
 * A path as an identifier, in monospace. It may break after a slash, and inside a
 * name only when a name alone is wider than the line.
 */
export function PathText({ path, className = '' }: { path: string; className?: string }) {
  return (
    <span className={`data font-mono [overflow-wrap:anywhere] ${className}`}>
      <bdi>
        {path.split('/').map((part, index) => (
          <Fragment key={index}>
            {index > 0 && (
              <>
                /<wbr />
              </>
            )}
            {part}
          </Fragment>
        ))}
      </bdi>
    </span>
  )
}
