# What agdash can read from WorkChord, and with which credential

agdash reads WorkChord; it never writes to it. This page records, from
WorkChord's own code, which credential can read each thing agdash uses. It was
checked against WorkChord commit `8efbcef87`, and the assignment and
project-task rows again at `3032919bd`; paths below are under `backend/app/`
in the WorkChord repository.

## How agdash connects

WorkChord reports its access mode at `GET /api/auth/me`, which needs no
credential (`http_authority.py:22`, `routers/identity.py:138`).

- **managed**: agdash pairs through WorkChord's native-connection flow
  (`routers/identity.py:79-112`, `services/native_session_service.py`). agdash
  starts a connection with an S256 proof. The person opens the verification
  page, signs in, compares the code and approves. agdash then exchanges its
  private verifier for a bearer session of that person. A connection lasts ten
  minutes, can be used once, and approval needs a signed-in human session
  (`native_session_service.py:25-28, 57, 107-131`). The session lasts as long as
  any WorkChord session (eight hours by default). agdash does not renew it; when
  it ends, pair again. (On servers before `2d2dac00a` a bearer session could
  renew itself without limit through `POST /api/auth/native-token`,
  `routers/identity.py:326-333`.) From `2d2dac00a`, agdash can pair as a
  read-only observer (`"access": "observe"`): that session lasts one day and
  renews, rotating its token, until 30 days after pairing.
- **trusted_local**: a request with no credential is let in as a local
  collaborator that reads everything unfiltered and may also write what an
  operator does not own (`http_authority.py:109-111`, `authority.py:54-55`);
  agdash still sends only GET. Servers before `2d2dac00a` refuse to start a
  pairing (`native_session_service.py:42-43`), so agdash connects with no
  credential. From `2d2dac00a`, the server's own browser can approve a
  read-only observer pairing, which names agdash and can be revoked.

On servers before `2d2dac00a` the paired session carries the person's full
rights, because WorkChord had no read-only token; from `2d2dac00a` an observer
session is kept read-only by the server. Either way, agdash's client refuses,
in code, every method but GET and HEAD. Pairing alone may POST, and only to the start and exchange
endpoints.

When a managed server lists `observer-sessions-v1` among its features, agdash
pairs as a read-only observer named `agdash`, and keeps the session only if the
server issued it read-only. On an older server it pairs a full session and says
so. `agdash connect list` and `agdash connect status ID` show which one a
connection holds. agdash does not renew either kind; when one ends, pair again.
Publish mode, when turned on, pairs a full session of its own for its one write
and never uses this one.

## Two credentials, never in one request

WorkChord rejects a request that carries credentials of two different
principals (`http_authority.py:100-102`). agdash keeps:

1. **The person's session** (managed mode), or nothing (trusted-local mode),
   for projects, iterations, tasks and the pipeline.
2. **An optional agent key with `planning:read`**, sent alone and only to the
   agent endpoints below that a person's session cannot read.

## Who may read what

"Person" is a native bearer session; "local" is no credential on a
trusted-local server; "agent" is an agent key with `planning:read`.
"Filtered" means limited to the projects the reader is a member of
(`authority.py:96-193`); operators and local readers are not filtered.

| Endpoint | Guard | Person | Local | Agent |
|---|---|---|---|---|
| `GET /projects`, `/iterations`, `/tasks/{id}`, `/tasks/lookup`, `/iterations/{id}/tasks`, `/projects/{id}/tasks` | global guard and row scoping; a project's tree is capped at 2,500 tasks (`query_limits.py`) | filtered | all | filtered |
| `GET /agent/pipeline` | `require_pipeline_read_access` (`routers/agent.py:252-266`) | filtered; needs a workspace role or a project | all | filtered |
| `GET /agent/assignments` | `get_assignment_reader` (`routers/agent.py:274-291`): an agent key reads its queue boundary; without one, people read the assignments of the tasks they can see (`services/agent_work_service.py:1127`). Servers before `3032919bd` need the agent key | filtered | all | yes |
| `GET /agent/runs/{id}/usage` | as `/agent/runs/{id}` | operators only | no | yes |
| `GET /agent/runs/{id}` | `require_agent_read_access` (`routers/agent.py:219-249`) | operators only | no | yes |
| `GET /agent/actors` | `get_agent_admin_actor` (`routers/agent.py:189-216`); limited to the caller's team topology (`services/agent_work_service.py:787-828`) | operators only | no | yes, its topology |
| `GET /agent/model-bindings` | `get_agent_actor`; `planning:read` (`services/agent_model_catalog_service.py:91-93`) | no | no | yes |
| `GET /team-member-profiles` | routes under `/team` need `team:read` for agents (`http_authority.py:144-145`) | all with a workspace role, else own | all | no (needs `team:read`) |

## Assignments and team topology

- **Assignments** are readable by a person's session or a local reader on
  current servers, limited to the tasks they can see; older servers need the
  agent key. Each row carries `task_id`, `actor_id`,
  `team_member_id`, `purpose`, `state` and the model binding; the project comes
  from the task (`schemas/agent.py:643-669`).
- **Team topology**: from `814f3032d`, `GET /agent/team-setup/topology`
  returns each member's actor, role, model binding keys and runtime reference
  to people with a workspace role, local readers and observers of such a
  person, and on the status rule to agent keys. Older servers show topology
  only through team-setup status and report, which only an operator or the
  topology's PM agent may read (`services/agent_team_setup_service.py:2546-2591`),
  and through the actor roster and model bindings above.
- **Runtime references** (`runtime_ref`) come from that topology read on
  current servers. On older servers no reader-facing endpoint returns them
  (status and report omit them; only the operator- or PM-only starting points
  carry the applied manifest), so agdash learns which harness runs an actor
  from what it observes on this host instead.
- In managed mode, an agent key's rows are further limited to the projects an
  operator granted that agent (`routers/identity.py:252-285`).

## What this means for agdash

- With a session alone, agdash maps sessions to projects and tasks, and shows
  the pipeline and assignments.
- The actor roster, model bindings and run detail need the agent key. Without
  it, agdash learns actors from the evidence it observes. A server that lists
  `person-run-reads-v1` among the features at `GET /api/auth/me` lets a
  session read its run list, with each run's declared harness session but no
  run detail.
- The operator admin key is never needed, and agdash does not ask for it.

## How agdash keeps its copy

The service polls each connected server every minute, and backs off, doubling
up to fifteen minutes, while the server cannot be reached. With the agent key,
agdash refreshes the runs it already knows of: those a session links to, and
those not yet ended. Without it, on a server that offers the run list to
people, agdash reads the newest 200 runs from that list. Actors come from the
roster when the agent key may read it, and are otherwise learned from task
claims and assignments, which name them.

Each kind of object (projects, tasks, pipeline, assignments, actors, runs) is
synced on its own, and `agdash connect status ID` shows how its last sync went:

- **ok**: the copy matches the server.
- **unavailable**: the stored credentials may not read it. The rest still syncs.
- **unparseable**: the server answered in a shape agdash does not know. The copy
  is left as it was, never filled with a guess, and the status names where the
  shape broke, not the values.
- **error**: the server failed or could not be reached; the next sync retries.

After each sync, agdash links sessions to runs and tasks from what it observed,
and keeps the evidence for each link, strongest first:

1. **tool_call**: the session's transcript holds the result of a WorkChord work
   call (begin, renew, submit or fail), which names the run and the task.
2. **trace_id**: a run's actor declared the session when it began.
3. **mcp_config**: an agent process was started with the server's address in
   its arguments. This links to the server, not to a task.
4. **heuristic**: the branch or worktree names a task key, and the session
   overlaps one of the task's runs. Kept only as a suggestion, and dropped once
   the session has an exact link.

`agdash connect sync ID` runs one sync now.

Then each session is mapped to a WorkChord actor and project. Each mapping
keeps the evidence it rests on, strongest first:

- **Actor**: `capabilities`, when the transcript holds a WorkChord call that
  names the calling actor; `run`, the actor of an exactly linked run;
  `runtime_ref`, a local rule tying a folder, repository or launcher to a team
  member's runtime reference, which the team topology names; `manual`, a pin.
- **Project**: `task`, the project of an exactly linked task; `assignment`, when
  every current assignment of the session's actor is in one project; `rule`, a
  local folder or repository rule, which covers sessions that never talk to
  WorkChord; `manual`, a pin.

A session with no evidence is **unmapped**. Evidence that disagrees is a
**conflict**: agdash shows every candidate and picks none. A pin is the
person's own answer and settles a conflict, while the disagreeing evidence
stays visible. `GET /api/sessions/ID/mapping` shows a session's mapping, and
`GET /api/mapping/unmapped` lists top-level sessions with no project.

The app does the same under **Settings → WorkChord** (connect a server, add a
folder rule, see each connection's sync status) and **Projects → Unmapped**
(each unmapped session with its evidence, and a pin). These are the only
requests the app sends that change anything, and they change agdash's own
settings: a few routes under `/api/workchord/` write through a connection that,
on SQLite, the database lets change only the connection, rule, pin and mapping
tables. Agent keys, webhook secrets and publish mode stay on the command line.

Rules and pins are local to agdash and never sent to WorkChord:

```bash
agdash connect rule ID --folder /work/orchard --project 1
agdash connect rule ID --repository git@github.example:acme/orchard --project 1
agdash connect rule ID --launcher 'codex *' --runtime-ref runtime://backend-worker
agdash connect pin ID SESSION --project 2
agdash connect rules ID
agdash connect unrule ID RULE
```

## Publish mode, optional

agdash only reads WorkChord, with one exception that is off unless you turn it
on for a server: publish mode. When it is on, each session with an exact task
link (a work call in its transcript, or a run that declared it) gets one
external link on that task, with provider `agdash`, pointing at the session in
agdash. Suggestions and server-only links never publish. agdash never posts run
events or usage, which in WorkChord are the actor's own declarations.

```bash
agdash connect publish ID on
agdash connect publish ID off
```

- The server must list `agdash-external-links-v1` among its features. agdash
  never falls back to another provider.
- On a managed server, turning it on pairs a second, full session through the
  browser, for this write alone. It is stored apart from the read session, and
  removed when you turn publish mode off. A trusted-local server takes the link
  with no credential.
- The writer can send only `POST /api/tasks/<id>/external-links`. Anything else
  is refused before it leaves the machine.
- A link is sent once per session and task. WorkChord also returns the existing
  link when the same session comes again, so a retry cannot duplicate it. A
  refusal is recorded and not retried.
- The link sends the harness, the session's id and a loopback address
  (`AGDASH_PUBLISH_LINK`, by default
  `http://127.0.0.1:8765/#/history/sessions/{session_id}`). It sends no title,
  prompt or path. WorkChord keeps the first link it gets, so a later change of
  address applies only to new links.

## Webhooks, optional

Polling is enough on its own. A webhook only makes agdash sync a connection as
soon as WorkChord reports a change, instead of at the next poll. The receiver is
off unless `AGDASH_WEBHOOK_PORT` is set. When it is on, it is a second listener
on agdash's loopback address, apart from the API. A delivery never reaches the
API, and nothing in a delivery is stored.

To use it:

1. Choose a signing secret, and give it to agdash with
   `agdash connect webhook-secret ID`.
2. In WorkChord, an operator adds an outgoing webhook target with the same
   secret, the address `http://127.0.0.1:<port>/workchord/<ID>`, and the events
   to send, such as `task.*` and `agent.*`.

WorkChord refuses loopback and private addresses as webhook targets unless its
`allow_private_egress_urls` setting is on, so this works only with a WorkChord
on the same host that allows it.

agdash accepts a delivery only when its `X-WorkChord-Signature-256` matches the
HMAC-SHA256 of the body under that connection's secret. Anything else, unsigned
deliveries included, is refused with 401. WorkChord signs no timestamp, so an
event id agdash has already accepted is answered without syncing again.

## What changed in WorkChord

[WorkChord changes that agdash relies on](workchord-changes.md) records the
eight changes made in WorkChord for agdash, with the code behind each gap and
the first WorkChord version that has each change.
