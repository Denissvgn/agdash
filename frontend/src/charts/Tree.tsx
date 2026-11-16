import { hierarchy, tree, type HierarchyPointNode } from 'd3-hierarchy'
import { linkHorizontal } from 'd3-shape'
import { useTranslation } from 'react-i18next'
import { Figure, type FigureStatus, type LegendEntry } from './Figure'
import { DOT, LINE, RING } from './geometry'
import { FILL } from './ink'
import { Plot, type Mark } from './Plot'
import { EVIDENCE_TIERS, TIER_DASH, tierCap, type EvidenceTier } from './tiers'
import { useWidth } from './useWidth'

export type TreeNode = {
  id: string
  name: string
  /** How this node is linked to its parent, and what the link says. */
  edge?: { tier: EvidenceTier; label?: string }
  children?: TreeNode[]
}

const ROW = 48
const PAD = 16
const NAME_ROOM = 120
const MIN_COLUMN = 72
const MAX_COLUMN = 220
/** Edge labels show where a column has this much room; elsewhere they are in the tooltip and table. */
const LABEL_ROOM = 120

type Point = HierarchyPointNode<TreeNode>

/**
 * A tree laid out left to right, such as a session and the agents it started. Each
 * edge's line follows the evidence tier of its link and carries a label. Up and
 * down move through the nodes in order; left goes to the parent, right to the
 * first child.
 */
export function Tree({
  title,
  caption,
  root,
  status = 'ready',
  onRetry,
  rowHeader,
}: {
  title: string
  caption?: string
  root?: TreeNode
  status?: FigureStatus
  onRetry?: () => void
  rowHeader: string
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const shown: FigureStatus = status === 'ready' && !root ? 'empty' : status
  const tier = (value: EvidenceTier) => t(`charts.tiers.${value}`)

  let plot = null
  let height = 4 * ROW
  let legend: LegendEntry[] = []
  const order: Point[] = []
  if (root) {
    const base = hierarchy(root)
    const depth = Math.max(1, base.height)
    const column = Math.max(MIN_COLUMN, Math.min(MAX_COLUMN, (width - PAD - NAME_ROOM) / depth))
    const laid = tree<TreeNode>().nodeSize([ROW, column])(base)
    laid.eachBefore((node) => {
      order.push(node)
    })
    const top = Math.min(...order.map((node) => node.x))
    const bottom = Math.max(...order.map((node) => node.x))
    height = bottom - top + 2 * PAD + 24
    const at = (node: Point) => ({ x: PAD + node.y, y: PAD + 16 + node.x - top })
    const index = new Map(order.map((node, i) => [node, i]))
    const chars = Math.max(6, Math.floor((column - 16) / 6.4))
    const clip = (name: string, room = chars) => (name.length > room ? `${name.slice(0, room - 1)}…` : name)
    const link = linkHorizontal<{ source: Point; target: Point }, Point>()
      .x((node) => at(node).x)
      .y((node) => at(node).y)
    const used = EVIDENCE_TIERS.filter((value) => order.some((node) => node.data.edge?.tier === value))
    legend = used.map((value) => ({
      key: value,
      label: tier(value),
      swatch: 'ink-6',
      kind: 'line',
      dash: TIER_DASH[value],
      cap: tierCap(value),
    }))

    const marks: Mark[] = order.map((node) => ({
      ...at(node),
      value: node.data.name,
      name:
        node.parent && node.data.edge
          ? t('charts.tree.via', {
              parent: node.parent.data.name,
              evidence: tier(node.data.edge.tier),
              label: node.data.edge.label ?? tier(node.data.edge.tier),
            })
          : t('charts.tree.root'),
    }))
    const navigate = (from: number, key: string) => {
      const node = order[from]
      if (key === 'ArrowLeft') return node.parent ? (index.get(node.parent) ?? from) : from
      if (key === 'ArrowRight') return node.children?.length ? (index.get(node.children[0]) ?? from) : from
      return undefined
    }

    plot = (
      <div ref={ref} className="min-w-0">
        <Plot
          height={height}
          label={title}
          marks={marks}
          navigate={navigate}
          pick={(px, py) => {
            let best: number | null = null
            let distance = ROW / 2
            order.forEach((node, i) => {
              const p = at(node)
              const d = Math.hypot(px - p.x, (py - p.y) * 2)
              const onName = px >= p.x && px <= p.x + column - 8 && Math.abs(py - p.y) <= 10
              if (onName || d < distance) {
                distance = onName ? 0 : d
                best = i
              }
            })
            return best
          }}
          width={width}
        >
          {({ active, mark }) => (
            <>
              {order.slice(1).map((node) => {
                const edge = node.data.edge
                const from = at(node.parent!)
                const to = at(node)
                const label = edge ? (edge.label ?? tier(edge.tier)) : null
                return (
                  <g key={`edge-${node.data.id}`} aria-hidden="true" data-tier={edge?.tier}>
                    <path
                      className="stroke-figure-mark"
                      d={link({ source: node.parent!, target: node }) ?? ''}
                      fill="none"
                      strokeDasharray={edge ? TIER_DASH[edge.tier] : undefined}
                      strokeLinecap={edge ? tierCap(edge.tier) : 'butt'}
                      strokeWidth={LINE}
                    />
                    {label && column >= LABEL_ROOM ? (
                      <text
                        className="fill-secondary-ink stroke-working-paper text-counter"
                        data-figure-data=""
                        dominantBaseline="hanging"
                        paintOrder="stroke"
                        strokeWidth={3}
                        textAnchor="end"
                        x={to.x - 10}
                        y={to.y + 5}
                      >
                        {clip(label, Math.floor((to.x - from.x) / 7))}
                      </text>
                    ) : null}
                  </g>
                )
              })}
              {order.map((node, i) => {
                const p = at(node)
                return (
                  <g key={node.data.id}>
                    <circle
                      {...mark(i)}
                      className={`${active === i ? FILL['mark-hover'] : FILL['ink-7']} stroke-working-paper`}
                      cx={p.x}
                      cy={p.y}
                      r={DOT / 2 + (active === i ? 1 : 0)}
                      strokeWidth={RING}
                    />
                    <text
                      className={`${active === i ? 'font-semibold' : ''} fill-primary-ink stroke-working-paper text-counter`}
                      data-figure-data=""
                      paintOrder="stroke"
                      strokeWidth={3}
                      x={p.x - 4}
                      y={p.y - 9}
                    >
                      {clip(node.data.name, node.children?.length ? chars : Math.max(chars, Math.floor((width - p.x - 12) / 6.4)))}
                    </text>
                  </g>
                )
              })}
            </>
          )}
        </Plot>
      </div>
    )
  }

  return (
    <Figure
      caption={caption}
      height={height}
      legend={legend}
      status={shown}
      table={
        root
          ? {
              rowHeader,
              columns: [
                { key: 'parent', label: t('charts.tree.parent') },
                { key: 'link', label: t('charts.tree.link') },
                { key: 'evidence', label: t('charts.tree.evidence') },
              ],
              rows: order.map((node) => ({
                key: node.data.id,
                header: node.data.name,
                cells: node.parent
                  ? [
                      node.parent.data.name,
                      node.data.edge?.label ?? (node.data.edge ? tier(node.data.edge.tier) : ''),
                      node.data.edge ? tier(node.data.edge.tier) : '',
                    ]
                  : [t('charts.tree.root'), '', ''],
              })),
            }
          : undefined
      }
      title={title}
      onRetry={onRetry}
    >
      {plot}
    </Figure>
  )
}
