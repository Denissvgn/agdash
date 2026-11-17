import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { getJson } from '../api/client'
import type { SessionDetail, SessionMapping } from '../api/types'
import { Tree, type FigureStatus } from '../charts'
import { mappingTree } from './mappingTree'

/**
 * A session's WorkChord mapping, drawn as session, agent, project and runs, each edge
 * styled and labelled by how the link is known. A session nothing maps says so. With
 * no WorkChord server connected the figure is left out.
 */
export function MappingFigure({ session }: { session: SessionDetail }) {
  const { t } = useTranslation()
  const mapping = useQuery({
    queryKey: ['mapping', 'session', session.id],
    queryFn: ({ signal }) => getJson<SessionMapping>(`/api/sessions/${session.id}/mapping`, {}, signal),
  })
  if (mapping.isSuccess && mapping.data.connections.length === 0) return null
  const connection = mapping.data?.connections[0]
  const root = connection ? mappingTree(session, connection, t) : null
  const status: FigureStatus = mapping.isError ? 'error' : mapping.isPending ? 'loading' : root ? 'ready' : 'empty'
  return (
    <Tree
      caption={connection ? t('session.mapping.caption', { server: connection.server }) : undefined}
      emptyBody={t('session.mapping.unmappedBody')}
      emptyTitle={t('session.mapping.unmappedTitle')}
      root={root ?? undefined}
      rowHeader={t('session.mapping.linked')}
      status={status}
      tierLabel={(tier) => t(`session.mapping.tiers.${tier}`)}
      title={t('session.mapping.title')}
      onRetry={() => void mapping.refetch()}
    />
  )
}
