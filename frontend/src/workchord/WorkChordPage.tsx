import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleAlert, ExternalLink, Plug, Trash2 } from 'lucide-react'
import { useEffect, useId, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { getJson, sendJson } from '../api/client'
import type { MappingRule, Pairing, WorkChordConnection } from '../api/types'
import { formatWhen } from '../history/format'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows, Notice } from '../history/SessionList'
import { CONNECTIONS, reasonOf, useConnections, useProjects } from './hooks'

const POLL_MS = 2000

/** A titled working-paper section of a Settings page. */
export function Section({ title, children, labelledBy }: { title: string; children: ReactNode; labelledBy: string }) {
  return (
    <section
      aria-labelledby={labelledBy}
      className="flex flex-col gap-md rounded-surface border border-hairline bg-working-paper p-[20px] shadow-resting"
    >
      <h2 className="text-title text-primary-ink" id={labelledBy}>
        {title}
      </h2>
      {children}
    </section>
  )
}

// -- connecting ---------------------------------------------------------------------

function Approval({ pairing, onDone, onCancel }: { pairing: Pairing; onDone: () => void; onCancel: () => void }) {
  const { t } = useTranslation()
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    if (!pairing.pairing_id) return
    let stopped = false
    const ask = async () => {
      try {
        const answer = await sendJson<Pairing>('POST', `/api/workchord/pairings/${pairing.pairing_id}/finish`)
        if (!stopped && answer?.status === 'connected') onDone()
      } catch (error) {
        if (stopped) return
        stopped = true
        setProblem(reasonOf(error) ?? '')
      }
    }
    const timer = window.setInterval(() => void ask(), POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [pairing.pairing_id, onDone])

  return (
    <div className="flex max-w-[40rem] flex-col gap-sm rounded-surface border border-strong-rule bg-canvas-mist p-md">
      <h3 className="text-body font-semibold text-primary-ink">{t('workchord.connect.approveTitle')}</h3>
      <p className="max-w-[70ch] text-body text-secondary-ink">{t('workchord.connect.approveBody')}</p>
      <p className="text-label text-secondary-ink">
        {pairing.read_only ? t('workchord.connect.approveReadOnly') : t('workchord.connect.approveFull')}
      </p>
      <p className="flex items-baseline gap-sm">
        <span className="text-label text-secondary-ink">{t('workchord.connect.code')}</span>
        <span className="data font-mono text-title text-primary-ink">{pairing.verification_code}</span>
      </p>
      <div className="flex flex-wrap items-center gap-sm">
        {pairing.verification_url && (
          <a className="button-primary" href={pairing.verification_url} rel="noreferrer" target="_blank">
            <ExternalLink aria-hidden="true" size={16} strokeWidth={1.75} />
            {t('workchord.connect.open')}
          </a>
        )}
        <button className="button-secondary" type="button" onClick={onCancel}>
          {t('workchord.connect.cancel')}
        </button>
      </div>
      <p aria-live="polite" className="text-label text-secondary-ink" role="status">
        {problem === null
          ? t('workchord.connect.waiting')
          : problem
            ? t('workchord.connect.failed', { reason: problem })
            : t('workchord.connect.failedUnknown')}
      </p>
    </div>
  )
}

function ConnectForm() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const addressId = useId()
  const nameId = useId()
  const hintId = useId()
  const [address, setAddress] = useState('')
  const [name, setName] = useState('')
  const [pairing, setPairing] = useState<Pairing | null>(null)
  const [connected, setConnected] = useState(false)
  const start = useMutation({
    mutationFn: () =>
      sendJson<Pairing>('POST', '/api/workchord/pairings', { address: address.trim(), name: name.trim() || null }),
    onSuccess: (answer) => {
      setConnected(false)
      if (answer?.status === 'connected') {
        setConnected(true)
        void client.invalidateQueries({ queryKey: CONNECTIONS })
      } else setPairing(answer)
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (address.trim()) start.mutate()
  }
  const done = () => {
    setPairing(null)
    setConnected(true)
    setAddress('')
    setName('')
    void client.invalidateQueries({ queryKey: CONNECTIONS })
  }

  return (
    <Section labelledBy="connect-title" title={t('workchord.connect.title')}>
      {pairing ? (
        <Approval pairing={pairing} onCancel={() => setPairing(null)} onDone={done} />
      ) : (
        <form className="flex max-w-[40rem] flex-col gap-sm" onSubmit={submit}>
          <div className="flex flex-col gap-xs">
            <label className="text-label text-secondary-ink" htmlFor={addressId}>
              {t('workchord.connect.address')}
            </label>
            <input
              aria-describedby={hintId}
              autoComplete="url"
              className="field"
              id={addressId}
              inputMode="url"
              required
              type="text"
              value={address}
              onChange={(event) => setAddress(event.target.value)}
            />
            <p className="text-label text-secondary-ink" id={hintId}>
              {t('workchord.connect.addressHint')}
            </p>
          </div>
          <div className="flex flex-col gap-xs">
            <label className="text-label text-secondary-ink" htmlFor={nameId}>
              {t('workchord.connect.name')}
            </label>
            <input
              autoComplete="off"
              className="field"
              id={nameId}
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div>
            <button aria-busy={start.isPending} className="button-primary" disabled={start.isPending} type="submit">
              <Plug aria-hidden="true" size={16} strokeWidth={1.75} />
              {start.isPending ? t('workchord.connect.starting') : t('workchord.connect.submit')}
            </button>
          </div>
        </form>
      )}
      <p aria-live="polite" className="text-label text-secondary-ink" role="status">
        {start.isError
          ? reasonOf(start.error)
            ? t('workchord.connect.failed', { reason: reasonOf(start.error) })
            : t('workchord.connect.failedUnknown')
          : connected
            ? t('workchord.connect.connected')
            : ''}
      </p>
    </Section>
  )
}

// -- rules --------------------------------------------------------------------------


function RuleList({ connectionId }: { connectionId: number }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const rules = useQuery({
    queryKey: ['workchord', 'rules', connectionId],
    queryFn: ({ signal }) => getJson<MappingRule[]>(`/api/workchord/connections/${connectionId}/rules`, {}, signal),
  })
  const remove = useMutation({
    mutationFn: (ruleId: number) => sendJson<null>('DELETE', `/api/workchord/rules/${ruleId}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['workchord', 'rules', connectionId] })
      void client.invalidateQueries({ queryKey: ['mapping'] })
    },
  })
  if (rules.isPending) return <LoadingRows label={t('workchord.rules.loading')} />
  if (rules.isError) return <ErrorNotice error={rules.error} scope="workchord.error" onRetry={() => void rules.refetch()} />
  if (rules.data.length === 0) return <p className="text-body text-secondary-ink">{t('workchord.rules.none')}</p>
  return (
    <ul className="divide-y divide-hairline rounded-surface border border-hairline">
      {rules.data.map((rule) => {
        const what = t(`workchord.rules.${rule.match}`, { value: rule.value ?? rule.session_id })
        const name = rule.target_name ?? rule.target_value
        const target =
          rule.target === 'project'
            ? t('workchord.rules.toProject', { name })
            : rule.target === 'actor'
              ? t('workchord.rules.toActor', { name })
              : t('workchord.rules.toRuntime', { name })
        return (
          <li key={rule.id} className="flex flex-wrap items-center gap-x-sm gap-y-xs px-md py-sm">
            <span className="min-w-0 flex-1 break-words text-body text-primary-ink">
              <bdi>{what}</bdi> <span className="text-secondary-ink">{target}</span>
              {rule.harness && (
                <span className="ml-sm text-label text-secondary-ink">
                  {t('workchord.rules.harnessOnly', {
                    harness: t(`history.harnesses.${rule.harness}`, { defaultValue: rule.harness }),
                  })}
                </span>
              )}
            </span>
            <button
              aria-label={t('workchord.rules.removeLabel', { what })}
              className="button-ghost"
              disabled={remove.isPending}
              type="button"
              onClick={() => remove.mutate(rule.id)}
            >
              <Trash2 aria-hidden="true" size={16} strokeWidth={1.75} />
              {t('workchord.rules.remove')}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export function ProjectSelect({
  connectionId,
  value,
  onChange,
  id,
  placeholderKey,
}: {
  connectionId: number
  value: string
  onChange: (value: string) => void
  id: string
  placeholderKey: string
}) {
  const { t } = useTranslation()
  const projects = useProjects(connectionId)
  return (
    <select
      className="field"
      disabled={!projects.data?.length}
      id={id}
      required
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">{t(placeholderKey)}</option>
      {projects.data?.map((project) => (
        <option key={project.id} value={String(project.id)}>
          {project.name}
        </option>
      ))}
    </select>
  )
}

function FolderRuleForm({ connectionId }: { connectionId: number }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const folderId = useId()
  const hintId = useId()
  const projectId = useId()
  const [folder, setFolder] = useState('')
  const [project, setProject] = useState('')
  const projects = useProjects(connectionId)
  const add = useMutation({
    mutationFn: () =>
      sendJson<MappingRule>('POST', `/api/workchord/connections/${connectionId}/rules`, {
        match: 'folder',
        value: folder.trim(),
        project_id: Number(project),
      }),
    onSuccess: () => {
      setFolder('')
      setProject('')
      void client.invalidateQueries({ queryKey: ['workchord', 'rules', connectionId] })
      void client.invalidateQueries({ queryKey: ['mapping'] })
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (folder.trim() && project) add.mutate()
  }
  return (
    <form className="flex max-w-[40rem] flex-col gap-sm" onSubmit={submit}>
      <h3 className="text-body font-semibold text-primary-ink">{t('workchord.rules.addTitle')}</h3>
      <div className="flex flex-col gap-xs">
        <label className="text-label text-secondary-ink" htmlFor={folderId}>
          {t('workchord.rules.folder_field')}
        </label>
        <input
          aria-describedby={hintId}
          autoComplete="off"
          className="field font-mono"
          id={folderId}
          required
          spellCheck={false}
          type="text"
          value={folder}
          onChange={(event) => setFolder(event.target.value)}
        />
        <p className="text-label text-secondary-ink" id={hintId}>
          {t('workchord.rules.folderHint')}
        </p>
      </div>
      <div className="flex flex-col gap-xs">
        <label className="text-label text-secondary-ink" htmlFor={projectId}>
          {t('workchord.rules.project')}
        </label>
        <ProjectSelect
          connectionId={connectionId}
          id={projectId}
          placeholderKey="workchord.rules.chooseProject"
          value={project}
          onChange={setProject}
        />
        {projects.data && projects.data.length === 0 && (
          <p className="text-label text-secondary-ink">{t('workchord.rules.noProjects')}</p>
        )}
      </div>
      <div>
        <button aria-busy={add.isPending} className="button-secondary" disabled={add.isPending} type="submit">
          {add.isPending ? t('workchord.rules.adding') : t('workchord.rules.add')}
        </button>
      </div>
      <p aria-live="polite" className="text-label text-secondary-ink" role="status">
        {add.isError
          ? reasonOf(add.error)
            ? t('workchord.rules.failed', { reason: reasonOf(add.error) })
            : t('workchord.rules.failedUnknown')
          : ''}
      </p>
    </form>
  )
}

// -- servers ------------------------------------------------------------------------

function ConnectionCard({ connection }: { connection: WorkChordConnection }) {
  const { t } = useTranslation()
  const synced = formatWhen(connection.last_synced_at)
  const titleId = `connection-${connection.id}`
  return (
    <section
      aria-labelledby={titleId}
      className="flex flex-col gap-md rounded-surface border border-hairline bg-working-paper p-[20px] shadow-resting"
    >
      <header className="flex flex-col gap-xs">
        <h2 className="text-title text-primary-ink" id={titleId}>
          {connection.name ?? t('workchord.unnamed')}
        </h2>
        <p className="data break-all font-mono text-label text-secondary-ink">{connection.server}</p>
        <p className="flex flex-wrap gap-x-sm text-label text-secondary-ink">
          <span className="font-semibold text-primary-ink">{t(`workchord.credential.${connection.credential}`)}</span>
          <span>{t(`workchord.credentialDetail.${connection.credential}`)}</span>
        </p>
        <p className="flex flex-wrap gap-x-sm text-label text-secondary-ink">
          <span>{synced ? t('workchord.lastSynced', { when: synced }) : t('workchord.neverSynced')}</span>
          {connection.agent_key && <span>{t('workchord.agentKey')}</span>}
          {connection.publish && <span>{t('workchord.publishOn')}</span>}
        </p>
        {connection.last_error && (
          <p className="flex items-start gap-xs text-label text-primary-ink">
            <CircleAlert aria-hidden="true" className="mt-[1px] shrink-0 text-feedback-warning" size={14} strokeWidth={1.75} />
            {t('workchord.syncProblem', { problem: connection.last_error })}
          </p>
        )}
      </header>
      {connection.objects.length > 0 && (
        <dl aria-label={t('workchord.objectsLabel')} className="grid grid-cols-[auto_1fr] gap-x-md gap-y-xs text-label">
          {connection.objects.map((object) => (
            <div key={object.kind} className="contents">
              <dt className="text-secondary-ink">{t(`workchord.objects.${object.kind}`, { defaultValue: object.kind })}</dt>
              <dd className="text-primary-ink">
                {t(`workchord.objectStates.${object.state}`)}
                {object.detail && <span className="ml-sm text-secondary-ink">{object.detail}</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <div className="flex flex-col gap-sm border-t border-hairline pt-md">
        <h3 className="text-body font-semibold text-primary-ink">{t('workchord.rules.title')}</h3>
        <p className="max-w-[70ch] text-label text-secondary-ink">{t('workchord.rules.purpose')}</p>
        <RuleList connectionId={connection.id} />
        <FolderRuleForm connectionId={connection.id} />
      </div>
    </section>
  )
}

export function WorkChordPage() {
  const { t } = useTranslation()
  const connections = useConnections()
  const base = 'workspaces.settings.destinations.workchord'
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${base}.purpose`} titleKey={`${base}.title`} />
      {connections.isPending ? (
        <LoadingRows label={t('workchord.loading')} />
      ) : connections.isError ? (
        <ErrorNotice error={connections.error} scope="workchord.error" onRetry={() => void connections.refetch()} />
      ) : connections.data.length === 0 ? (
        <Notice body={t('workchord.noneBody')} title={t('workchord.noneTitle')} />
      ) : (
        <div aria-label={t('workchord.servers')} className="flex flex-col gap-md" role="group">
          {connections.data.map((connection) => (
            <ConnectionCard key={connection.id} connection={connection} />
          ))}
        </div>
      )}
      <ConnectForm />
    </article>
  )
}
