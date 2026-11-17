import type { MappingResolution, SessionDetail, SessionMapping } from '../api/types'
import type { EvidenceTier, TreeNode } from '../charts'
import { nameOf } from '../history/names'

/**
 * How sure each kind of mapping is, as the tree's line styles: a recorded link
 * (a pin, the session's own run or task) is solid; a matching record (the agent's
 * own call, its assignments, a runtime rule) long dashes; a folder or repository
 * rule short dashes; a suggestion dots.
 */
const BASIS_TIER: Record<string, EvidenceTier> = {
  manual: 'tool_call',
  run: 'tool_call',
  task: 'tool_call',
  capabilities: 'trace_id',
  runtime_ref: 'trace_id',
  assignment: 'trace_id',
  rule: 'mcp_config',
}

/** A mapping as a tree edge: the decided target, or a suggestion on a conflict, or none. */
function resolved(
  resolution: MappingResolution,
  kind: 'actor' | 'project',
  t: (key: string, options?: Record<string, unknown>) => string,
): TreeNode | null {
  const what = t(`session.mapping.${kind}`)
  if (resolution.state === 'mapped' && resolution.basis) {
    return {
      id: kind,
      name: t('session.mapping.node', { what, name: resolution.name ?? `#${resolution.target}` }),
      edge: { tier: BASIS_TIER[resolution.basis] ?? 'trace_id', label: t(`mapping.basis.${resolution.basis}`, { defaultValue: resolution.basis }) },
    }
  }
  if (resolution.state === 'conflict' && resolution.suggestion !== null) {
    // Weak or disputed evidence reads as a suggestion, never as the mapping.
    const named = resolution.evidence.find((item) => item.target === resolution.suggestion)
    return {
      id: kind,
      name: t('session.mapping.node', { what, name: named?.name ?? `#${resolution.suggestion}` }),
      edge: { tier: 'heuristic', label: t('session.mapping.suggestion') },
    }
  }
  return null
}

/** The tree of one session's mapping: the session, then its agent, project, and runs with their tasks. */
export function mappingTree(
  session: SessionDetail,
  mapping: SessionMapping['connections'][number],
  t: (key: string, options?: Record<string, unknown>) => string,
): TreeNode | null {
  const children: TreeNode[] = []
  const actor = resolved(mapping.actor, 'actor', t)
  const project = resolved(mapping.project, 'project', t)
  if (actor) children.push(actor)
  if (project) children.push(project)
  for (const [i, run] of mapping.runs.entries()) {
    const task = run.task_key ? t('board.taskWithKey', { key: run.task_key, title: run.task_title ?? '' }) : (run.task_title ?? null)
    children.push({
      id: `run-${i}`,
      name: run.run_id
        ? t('session.mapping.run', { id: run.run_id, task: task ?? t('session.mapping.noTask') })
        : t('session.mapping.taskOnly', { task: task ?? t('session.mapping.noTask') }),
      edge: {
        tier: run.suggestion ? 'heuristic' : run.basis,
        label: run.suggestion ? t('session.mapping.suggestion') : t(`charts.tiers.${run.basis}`),
      },
    })
  }
  if (!children.length) return null
  return { id: 'session', name: nameOf(session) ?? t('history.list.untitled'), children }
}
