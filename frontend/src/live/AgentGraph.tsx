import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { SessionSummary } from '../api/types'
import { useWidth } from '../charts/useWidth'
import { StateDot } from '../history/SessionList'
import type { Group, LiveGroup, Node } from './board'
import type { LinkTo } from './parts'
import { GroupHeading } from './Roster'
import { useRowKeys } from './words'

const NODE = 24
const PITCH = 28
const PAD = 16
/** Columns side by side need this much each; narrower, sessions indent under their parent. */
const MIN_COLUMN = 200
const MAX_NODE = 320
const BETWEEN = 40
const INDENT = 20

type Spot = { node: Node; depth: number; row: number; parent?: Spot }

/** Where each session of some trees sits: a row each, or a parent level with its first child. */
function place(nodes: Node[], beside: boolean, depth = 0, parent?: Spot, next = { row: 0 }, out: Spot[] = []): Spot[] {
  for (const node of nodes) {
    const spot: Spot = { node, depth, row: next.row, parent }
    out.push(spot)
    if (!beside || node.children.length === 0) next.row += 1
    place(node.children, beside, depth + 1, spot, next, out)
  }
  return out
}

function deepest(nodes: Node[]): number {
  return nodes.reduce((most, node) => Math.max(most, node.children.length ? 1 + deepest(node.children) : 0), 0)
}

const dashed = (session: SessionSummary) => session.parent_basis === 'process_tree'

/**
 * The board's sessions as a graph: a node a session, a line from each session to those
 * it started, solid where the harness recorded the link and dashed where it is known
 * from the process tree. The nodes are the same links as the timeline's rows, in the
 * same order, so the timeline is this drawing's table.
 */
export function AgentGraph({
  groups,
  by,
  baseId,
  selected,
  to,
}: {
  groups: Group[]
  by: LiveGroup
  baseId: string
  selected: number | null
  to: LinkTo
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const onKeyDown = useRowKeys()
  const levels = Math.max(0, ...groups.map((group) => deepest(group.nodes))) + 1
  const column = (width - 2 * PAD) / levels
  const beside = column >= MIN_COLUMN
  const x = (depth: number) => PAD + depth * (beside ? column : INDENT)
  const wide = (depth: number) => (beside ? Math.min(column - BETWEEN, MAX_NODE) : width - PAD - x(depth))
  const y = (row: number) => 6 + row * PITCH
  const linked = groups.some((group) => group.nodes.some((node) => node.children.length > 0))
  const styles = new Set(groups.flatMap((group) => place(group.nodes, beside)).filter((spot) => spot.parent).map((spot) => dashed(spot.node.session)))
  return (
    <div ref={ref} data-agents="">
      {groups.map((group, index) => {
        const id = `${baseId}-group-${index}`
        const spots = place(group.nodes, beside)
        const height = y(Math.max(...spots.map((spot) => spot.row)) + 1)
        return (
          <section key={String(group.key)} aria-labelledby={id} className="border-b border-hairline last:border-b-0">
            <GroupHeading by={by} count={group.count} group={group.key} id={id} others={group.others} />
            <div className="relative" style={{ height }}>
              <svg aria-hidden="true" className="absolute inset-0" height={height} width={width}>
                {spots.map((spot) => {
                  const from = spot.parent
                  if (!from) return null
                  const toY = y(spot.row) + NODE / 2
                  const d = beside
                    ? `M${x(from.depth) + wide(from.depth)} ${y(from.row) + NODE / 2} C${x(spot.depth) - BETWEEN / 2} ${y(from.row) + NODE / 2} ${x(spot.depth) - BETWEEN / 2} ${toY} ${x(spot.depth)} ${toY}`
                    : `M${x(from.depth) + 10} ${y(from.row) + NODE} V${toY} H${x(spot.depth)}`
                  return (
                    <path
                      key={spot.node.session.id}
                      className="stroke-figure-mark"
                      d={d}
                      data-edge={spot.node.session.parent_basis ?? 'unknown'}
                      fill="none"
                      strokeDasharray={dashed(spot.node.session) ? '4 3' : undefined}
                      strokeWidth={1.25}
                    />
                  )
                })}
              </svg>
              <ul>
                {spots.map((spot) => {
                  const { session } = spot.node
                  const from = spot.parent?.node.session
                  return (
                    <li key={session.id} className="absolute" style={{ left: x(spot.depth), top: y(spot.row), width: wide(spot.depth) }}>
                      <Link
                        aria-current={selected === session.id ? 'true' : undefined}
                        className="flex h-[24px] items-center gap-sm rounded-compact border border-hairline bg-working-paper px-sm text-label text-primary-ink hover:bg-canvas-mist focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-planning-thread aria-[current=true]:border-planning-thread aria-[current=true]:bg-planning-thread-soft"
                        data-row=""
                        data-selected={selected === session.id}
                        data-session={session.id}
                        replace
                        to={to(session.id)}
                        onKeyDown={onKeyDown}
                      >
                        {/* The node is the box; inside it the state is a dot and its word, not a pill in a box. */}
                        <span className="flex shrink-0 items-center gap-xs text-secondary-ink">
                          <StateDot state={session.state} />
                          {t(`history.states.${session.state}`)}
                        </span>
                        <span className="truncate font-semibold">{session.title || t('history.list.untitled')}</span>
                        {from ? (
                          <span className="sr-only">
                            {t('live.agents.startedBy', {
                              title: from.title || t('history.list.untitled'),
                              basis: t(`live.parentBasis.${session.parent_basis ?? 'unknown'}`, {
                                defaultValue: t('live.parentBasis.unknown'),
                              }),
                            })}
                          </span>
                        ) : null}
                      </Link>
                    </li>
                  )
                })}
              </ul>
            </div>
          </section>
        )
      })}
      <p className="flex flex-wrap items-center gap-x-md gap-y-xs border-t border-hairline px-md py-sm text-label text-secondary-ink" data-agents-key="">
        {linked ? (
          <>
            {[false, true]
              .filter((style) => styles.has(style))
              .map((style) => (
                <span key={String(style)} className="flex items-center gap-xs">
                  <svg aria-hidden="true" height={8} width={24}>
                    <line className="stroke-figure-mark" strokeDasharray={style ? '4 3' : undefined} strokeWidth={1.25} x1={0} x2={24} y1={4} y2={4} />
                  </svg>
                  {t(style ? 'live.agents.processTree' : 'live.agents.harnessRecord')}
                </span>
              ))}
          </>
        ) : (
          t('live.agents.none')
        )}
      </p>
    </div>
  )
}
