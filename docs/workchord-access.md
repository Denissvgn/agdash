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
  the pipeline and assignments.
- The actor roster, model bindings and runs need the agent key. Without it,
  agdash learns actors from the evidence it observes and shows no run detail.
- The operator admin key is never needed, and agdash does not ask for it.

## How agdash keeps its copy

The service polls each connected server every minute, and backs off, doubling
up to fifteen minutes, while the server cannot be reached. Runs have no list
endpoint, so agdash refreshes only the runs it already knows of: those a
session links to, and those not yet ended. Actors come from the roster when the
agent key may read it, and are otherwise learned from task claims and
assignments, which name them.

Each kind of object (projects, tasks, pipeline, assignments, actors, runs) is
synced on its own, and `agdash connect status ID` shows how its last sync went:

- **ok**: the copy matches the server.
- **unavailable**: the stored credentials may not read it. The rest still syncs.
- **unparseable**: the server answered in a shape agdash does not know. The copy
  is left as it was, never filled with a guess, and the status names where the
  shape broke, not the values.
- **error**: the server failed or could not be reached; the next sync retries.

`agdash connect sync ID` runs one sync now.
