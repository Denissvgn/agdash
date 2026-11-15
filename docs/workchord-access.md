# What agdash can read from WorkChord, and with which credential

agdash reads WorkChord; it never writes to it. This page records, from
WorkChord's own code, which credential can read each thing agdash uses. It was
checked against WorkChord commit `8efbcef87`; paths below are under
`backend/app/` in the WorkChord repository.

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
  any WorkChord session (eight hours by default) and is not renewed; when it
  ends, pair again.
- **trusted_local**: a request with no credential is let in as a local reader
  that sees everything unfiltered (`http_authority.py:109-111`). There is no
  signed-in person to approve a pairing, so agdash connects with no credential.

The paired session carries the person's full rights; WorkChord has no
read-only token. agdash's client therefore refuses, in code, every method but
GET and HEAD. Pairing alone may POST, and only to the start and exchange
endpoints.

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
| `GET /projects`, `/iterations`, `/tasks/{id}`, `/tasks/lookup`, `/iterations/{id}/tasks` | global guard and row scoping | filtered | all | filtered |
| `GET /agent/pipeline` | `require_pipeline_read_access` (`routers/agent.py:252-266`) | filtered; needs a workspace role or a project | all | filtered |
| `GET /agent/assignments` | `get_agent_actor` (`routers/agent.py:157-173`); `assignments:read` or `planning:read` (`services/agent_work_service.py:1071-1076`) | no | no | yes |
| `GET /agent/runs/{id}` | `require_agent_read_access` (`routers/agent.py:219-249`) | operators only | no | yes |
| `GET /agent/actors` | `get_agent_admin_actor` (`routers/agent.py:189-216`); limited to the caller's team topology (`services/agent_work_service.py:787-828`) | operators only | no | yes, its topology |
| `GET /agent/model-bindings` | `get_agent_actor`; `planning:read` (`services/agent_model_catalog_service.py:91-93`) | no | no | yes |
| `GET /team-member-profiles` | routes under `/team` need `team:read` for agents (`http_authority.py:144-145`) | all with a workspace role, else own | all | no (needs `team:read`) |

## Assignments and team topology

- **Assignments** need the agent key: neither a person's session nor a local
  reader can list them. Each row carries `task_id`, `actor_id`,
  `team_member_id`, `purpose`, `state` and the model binding; the project comes
  from the task (`schemas/agent.py:643-669`).
- **Team topology** has no read endpoint of its own. It shows through
  team-setup status and report, which only an operator or the topology's PM
  agent may read (`services/agent_team_setup_service.py:2546-2591`), and
  through the actor roster and model bindings above.
- **Runtime references** (`runtime_ref`) are part of the team-setup input
  (`schemas/agent_team_setup.py:191-209`) and no read endpoint returns them.
  agdash learns which harness runs an actor from what it observes on this host
  instead.
- In managed mode, an agent key's rows are further limited to the projects an
  operator granted that agent (`routers/identity.py:252-285`).

## What this means for agdash

- With a session alone, agdash maps sessions to projects and tasks, and shows
  the pipeline.
- Agents, assignments, model bindings and runs need the agent key. Without it,
  agdash learns actors from the evidence it observes and shows no run detail.
- The operator admin key is never needed, and agdash does not ask for it.
