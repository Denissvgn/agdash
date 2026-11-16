# WorkChord changes that agdash relies on

agdash reads WorkChord to map each session on this host to a WorkChord agent,
project and task. Building that connector showed eight gaps in what WorkChord
let a reader do. This page records each one:

- **Gap**: what WorkChord did at commit `8efbcef87`, with the code that shows it.
- **Change**: what WorkChord does now. All eight are merged in the WorkChord
  repository; the merge commit is the first WorkChord version that has it.
- **Touches**: the WorkChord files and contracts the change touched.
- **Unblocks**: the agdash feature it serves.

Paths are relative to the WorkChord repository root. Every change is
backward-compatible for older clients; a server older than the stated merge
commit behaves as described under **Gap**, and agdash must keep handling that.

| # | Change | WorkChord pull request | Merge commit | Unblocks |
|---|---|---|---|---|
| 1 | Read-only observer sessions | [#42](https://github.com/Denissvgn/WorkChord/pull/42) | `2d2dac00a` | Connecting without a person's full rights |
| 2 | `agdash` external-link provider | [#45](https://github.com/Denissvgn/WorkChord/pull/45) | `efde17bd3` | Publish mode |
| 3 | Harness session declared on runs | [#46](https://github.com/Denissvgn/WorkChord/pull/46) | `743e45084` | Exact session-to-run links |
| 4 | Advisory runtime observation intake | [#47](https://github.com/Denissvgn/WorkChord/pull/47) | `79bdf1521` | Reporting what agdash observed, if agdash opts in |
| 5 | People can read assignments | [#43](https://github.com/Denissvgn/WorkChord/pull/43) | `dd61c542b` | Actor and project mapping without an agent key |
| 6 | Team topology read with runtime references | [#44](https://github.com/Denissvgn/WorkChord/pull/44) | `814f3032d` | Mapping a session to an actor by runtime reference |
| 7 | Pairing and identity on trusted-local servers | [#42](https://github.com/Denissvgn/WorkChord/pull/42) | `2d2dac00a` | Connecting to a trusted-local server as a named reader |
| 8 | Capped renewal for unattended sessions | [#42](https://github.com/Denissvgn/WorkChord/pull/42) | `2d2dac00a` | Running as a background service past one session |

## 1. Read-only observer sessions

**Gap.** A paired native session was a full session of the person who approved
it. Sessions had no access level (`backend/app/models/user_session.py:16-30`),
and a bearer session resolved to that person's full authority
(`backend/app/http_authority.py:87-99`). agdash's client refuses every method
but GET and HEAD in its own code, but the token it held could write.

**Change.** Pairing takes `"access": "observe"` and a `client_label`. The
exchange returns an observer session that reads exactly what the approving
person can read. The server refuses every other request with
`read_only_credential`, except sign-out, renewal and recording an observation
(change 4). The refusal is enforced in three layers:

- the request guard;
- the authority check;
- the database write checks, with no exemption for personal records.

An observer must be the only credential in its request and cannot approve other
pairings. People see and revoke their readers under **Settings → Access →
Connected readers**.

**Touches.**
- Files:
  - `backend/app/http_authority.py`, `backend/app/authority.py`;
  - `backend/app/routers/identity.py`;
  - `backend/app/services/native_session_service.py`,
    `backend/app/services/identity_service.py`,
    `backend/app/services/session_service.py`;
  - `backend/app/models/user_session.py`,
    `backend/app/models/native_connection.py`;
  - `backend/app/config.py`;
  - migration `backend/app/migrations/versions/20261115_0014_observer_sessions.py`;
  - `frontend/src/pages/NativeConnectionPage.tsx`,
    `frontend/src/components/settings/ConnectedReadersPanel.tsx`;
  - `docs/identity-and-recovery.md`.
- Contracts:
  - `POST /api/auth/native-connections/start` (`access`, `client_label`);
  - `GET /api/auth/native-connections/{id}` (`access`, `client_label`);
  - the token response (`access`, `renewable_until`);
  - `GET /api/auth/me` (`access`, `observer`);
  - `GET` and `DELETE /api/auth/sessions` (`access`, `client_label`);
  - the error code `read_only_credential`;
  - the `user_sessions.access`, `user_sessions.client_label`,
    `native_connections.requested_access` and `native_connections.client_label`
    columns.

**Unblocks.** Connecting to WorkChord without holding a token that can write.
agdash's GET-only guard stays as a second layer.

**Note for agdash.** Publish mode adds a link to a task, which an observer
session cannot do. Publish mode therefore needs a full pairing of its own on a
managed server; on a trusted-local server it can still post without a
credential.

## 2. An `agdash` external-link provider

**Gap.**
- The providers were `github`, `gitlab`, `figma`, `sentry` and `custom`
  (`backend/app/schemas/external_link.py:27-33`), enforced by a database check
  (`backend/app/models/external_link.py:37-40`).
- Display links refused loopback hosts (`backend/app/utils/url_policy.py:42-70`,
  `:119-121`). A session link to agdash on `127.0.0.1` could not be stored at
  all, even as `custom`.

**Change.**
- Links accept `provider: "agdash"`.
- Only an `agdash` link may use a loopback URL (`localhost`, `127.0.0.0/8`,
  `[::1]`). The server never fetches display links. Private network
  addresses, credentials and other schemes stay refused, and other providers
  keep the old rule.
- Repeating an `agdash` link with the same `external_key` on the same task
  returns the existing link.
- An update re-checks the provider and URL together.
- The task timeline shows these links as "Observed session" and notes that a
  loopback link opens on the computer that observed the session.

**Touches.**
- Files:
  - `backend/app/schemas/external_link.py`, `backend/app/models/external_link.py`;
  - `backend/app/utils/url_policy.py`;
  - `backend/app/services/external_link_service.py`, `backend/app/routers/tasks.py`;
  - migration `backend/app/migrations/versions/20261116_0015_agdash_link_provider.py`;
  - `frontend/src/components/tasks/TaskTimelinePanel.tsx`, `frontend/src/types/task.ts`;
  - `docs/task-domain.md`.
- Contracts:
  - `POST /api/tasks/{task_id}/external-links` and `PUT /api/external-links/{id}`
    (the `agdash` provider and its URL rule);
  - the `ck_external_links_provider` check.

**Unblocks.** Publish mode: one link per observed session, without duplicates on
retry.

## 3. The harness session on a run

**Gap.**
- The worker role package sent a per-attempt external run id as `trace_id`
  (`agent-skills/workchord-worker/references/claim-run-and-task-state.md:139-143`).
- Nothing on a run named the harness session that performed it, so the
  session-to-run link fell back to the weaker evidence tiers.

**Change.** The worker role package (worker 1.14.0, catalog 1.15.0) sends the
runtime's session identifier once at begin, as run metadata. It does this only
when the runtime gives it one:

```json
{"metadata": {"harness": {"kind": "claude-code", "session_id": "<id>"}}}
```

- **`trace_id` stays the per-attempt run id.** One harness session can serve
  several assignments, and the worker's idempotency keys are built from
  `trace_id`, so they would collide if it carried the session id.
- **Resumed sessions:** a resumed session with a new id adds the same object to
  its next checkpoint event.
- **Validation:** WorkChord checks the shape: a short lowercase `kind`, and a
  `session_id` that is not a path and nothing credential-shaped.
- **Not verified:** the value is the actor's declaration; WorkChord stores it
  without checking it.

**Touches.**
- Files:
  - `agent-skills/workchord-worker/SKILL.md` and
    `agent-skills/workchord-worker/references/claim-run-and-task-state.md`,
    plus the generated `agent-skills/catalog.json`,
    `agent-skills/release-baseline.json` and
    `adapters/codex/workchord-agent-roles/`;
  - `scripts/build_agent_skills.py`;
  - `backend/app/schemas/agent.py`;
  - `config/examples/agent-team-master.json`,
    `config/model-aware-routing-closeout.json`;
  - `docs/external-agent-runtime.md`.
- Contracts:
  - `metadata.harness` on `POST /api/agent/me/work/begin`, model-aware begin
    and run start, returned as `metadata` on the run;
  - the worker package checksum.

**Unblocks.** The run-and-task link extractor. Its second evidence tier reads
`metadata.harness.session_id` from the run, not `trace_id`. Run detail still
needs the agent key with `planning:read`.

## 4. An advisory intake for runtime observations

**Gap.**
- WorkChord states runtime availability as `availability_unknown`
  (`backend/app/schemas/agent_team_setup.py:973`, `:1082-1109`) "without an
  independent observation", but it had nowhere to record one.
- If an observation could steer dispatch, a dashboard could affect agents.

**Change.** `POST /api/agent/observations` records one observation:

- a `kind`: `session_active`, `session_idle`, `session_ended`, or
  `model_observed` with `observed_model`;
- the harness kind and session id, the observation time and an idempotency key;
- optional task, run and actor references.

Only a paired observer or an operator may record one. References must be
readable by the observer, and an observer names an actor only through a run or
assignment of such a task. People, observers and operators read observations
with `GET /api/agent/observations`, within the tasks they can see. Operators
remove old rows with `POST /api/agent/observations/cleanup`.

**The intake is advisory: it has no effect on dispatch.**

- Recording an observation writes only its own row. It does not change any
  actor, `last_seen_at`, queue revision, assignment, run, task event or
  topology member, and it sends no webhook.
- No routing, readiness, assignment or work decision reads the table. A
  WorkChord test checks that none of those modules refers to it.
- Agent keys cannot read observations, so an agent cannot steer on them either.
- Team status and report keep `availability_unknown`.
- A WorkChord test records `session_ended` and a different `model_observed` for
  every member of a fully ready team. It then checks that team status, report
  availability, every actor's `last_seen_at`, queue revision, lifecycle and
  scopes, the topology members, and the assignment, run and task-event counts
  are all unchanged.

**Touches.**
- Files:
  - `backend/app/models/runtime_observation.py`;
  - `backend/app/schemas/runtime_observation.py`;
  - `backend/app/services/runtime_observation_service.py`;
  - `backend/app/routers/agent.py`, `backend/app/http_authority.py`,
    `backend/app/config.py`;
  - migration `backend/app/migrations/versions/20261116_0016_runtime_observations.py`;
  - `docs/agent-team-setup.md`.
- Contracts:
  - `POST` and `GET /api/agent/observations`,
    `POST /api/agent/observations/cleanup`;
  - the `agent_runtime_observations` table;
  - `AGENT_OBSERVATION_HOURLY_LIMIT` (600) and
    `AGENT_OBSERVATION_RETENTION_DAYS` (30).

**Unblocks.** Reporting what agdash observed, such as a session that ended
while its run is still marked running, or a model that differs from the
binding. Today agdash only reads WorkChord, and its single permitted write is
the opt-in publish mode. Sending observations would be a second write, so
adopting it is an owner decision. It would be opt-in, per server, like publish
mode.

## 5. People can read assignments

**Gap.** `GET /api/agent/assignments` accepted only an agent key:

- `get_agent_actor` (`backend/app/routers/agent.py:157-173`) on the route
  (`:720-744`);
- `assignments:read` or `planning:read`
  (`backend/app/services/agent_work_service.py:1071-1076`).

A person's session, even a project manager's, could not see who was assigned
to their tasks.

**Change.**
- **Who may read:** without an agent key, the endpoint admits people on the
  pipeline's read rule: a workspace role, a project membership, a
  trusted-local browser, or a paired observer. Operators are admitted too.
- **Which rows:** rows follow the task read scope.
- **What people get:** `AgentAssignmentReadItem` carries:
  - the actor's name and role;
  - the purpose, state and queue position;
  - the binding's declared model (`model_catalog_key`, `provider`,
    `configured_model_alias`) and the binding's status.
- **What they don't get:** routing evidence, the assigner's reason and
  reviewer details stay with agent-key reads, which are unchanged.

**Touches.**
- Files:
  - `backend/app/routers/agent.py`;
  - `backend/app/schemas/agent.py`;
  - `backend/app/services/agent_work_service.py`;
  - `frontend/src/components/agent/AssignedAgentsSummary.tsx`,
    `frontend/src/components/agent/TaskRoutingPanel.tsx`,
    `frontend/src/services/agentService.ts`, `frontend/src/types/agent.ts`;
  - `docs/identity-and-recovery.md`.
- Contracts: `GET /api/agent/assignments` without an agent key, and
  `AgentAssignmentReadItem`.

**Unblocks.**
- The agent and project resolver's `assignment` tier, with a session alone.
- Showing on a session whether its assignment is execution or review.
- Showing the model WorkChord bound, to compare with the model observed.

## 6. A team topology read that returns runtime references

**Gap.**
- The status and report reads (`backend/app/routers/agent.py:507-548`) never
  return runtime references: member status has no `runtime_ref`
  (`backend/app/schemas/agent_team_setup.py:940-975`). Only an operator or the
  topology's PM agent may read them
  (`backend/app/services/agent_team_setup_service.py:2546-2592`).
- People could not read the team at all.
- The first description of this gap said `runtime_ref` was never stored or
  returned. That is wrong:
  - it is stored (`backend/app/models/agent.py:441`);
  - `GET /api/agent/team-setup/starting-points`
    (`backend/app/routers/agent.py:491-504`) returns the applied manifest
    (`backend/app/services/agent_team_setup_service.py:2643-2644`), so an
    operator or PM agent could already read it, next to credential references.

**Change.**
- `GET /api/agent/team-setup/topology` returns each member's actor key and id,
  name, role, profile, lifecycle, runtime readiness, model binding keys and
  `runtime_ref`. It never returns `credential_ref`, handoffs or acknowledgement
  payloads.
- Agent keys read it on the status rule: an operator reads any topology, a PM
  agent only its own.
- Without an agent key, people with a workspace role, a trusted-local browser,
  a paired observer of such a person, and operators may read it. A project
  role alone does not admit a person, because the team is workspace-wide.
- The MCP tool `agent_get_team_topology` mirrors it for agents.

**Touches.**
- Files:
  - `backend/app/routers/agent.py`;
  - `backend/app/schemas/agent_team_setup.py`;
  - `backend/app/services/agent_team_setup_service.py`;
  - `backend/app/mcp_server.py`, `backend/app/mcp_agent_tools.py`;
  - `docs/agent-team-setup.md`.
- Contracts: `GET /api/agent/team-setup/topology` (`agent-team-topology-v1`)
  and the MCP tool `agent_get_team_topology`.

**Unblocks.**
- The agent and project resolver's `runtime_ref` tier, which ties a team
  member's runtime reference to a harness and a folder or launcher on this host.
- The project views' list of agents per project.

## 7. Pairing and identity on trusted-local servers

**Gap.**
- A trusted-local server refused to start any pairing
  (`backend/app/services/native_session_service.py:42-43`), and approval needed
  a signed-in person (`:25-29`).
- So agdash connected without a credential and could not be named or revoked.
- The first description said such a caller was an anonymous local *reader*.
  It is not. It is a local collaborator that may read and write what an
  operator does not own (`backend/app/http_authority.py:109-111`,
  `backend/app/authority.py:54-55`).

**Change.**
- A trusted-local server pairs read-only observers, approved from its local
  browser. A pairing that asks for full access is still refused.
- The observer is attributed to its own session and label rather than to a
  person.
- The local browser lists and revokes it under **Connected readers**.
- If the server changes to managed mode, the observer stops working.

**Touches.** The same files as change 1, and the same contracts. In addition,
`GET` and `DELETE /api/auth/sessions` now serve a trusted-local browser that has
no signed-in person.

**Unblocks.**
- Connecting to a trusted-local server as a named, revocable, read-only reader.
- Showing that connection in the connector screen.

## 8. Capped renewal for unattended sessions

**Gap.**
- `POST /api/auth/native-token` (`backend/app/routers/identity.py:326-333`)
  issued a new session to any human session, bearer sessions included.
- Each new session had a fresh sign-in time and a fresh eight-hour expiry
  (`backend/app/services/identity_service.py:229-232`,
  `backend/app/config.py:126`).
- The first description said unattended renewal was unclear. In fact a bearer
  session could renew itself forever, and nothing said whether that was
  intended.

**Change.**
- Renewal issues a successor at the same access level and with the original
  sign-in time. An observer is never upgraded to a full session.
- A bearer caller is rotated: its old token stops working.
- Renewal stops at an absolute cap from sign-in or pairing:
  `AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS` (seven days) for people, and
  `AUTH_OBSERVER_ABSOLUTE_MAX_AGE_SECONDS` (30 days) for observers.
- An observer session lasts `AUTH_OBSERVER_SESSION_MAX_AGE_SECONDS` (one day).
- `/api/session/rotate` shares the cap.
- Responses state `renewable_until`. After that time, the person signs in, or
  the reader pairs, again.

**Touches.** `backend/app/routers/identity.py`,
`backend/app/services/identity_service.py`,
`backend/app/services/session_service.py`, `backend/app/config.py`, the compose
files and `.env.example`, and `docs/identity-and-recovery.md`. The contracts
are `POST /api/auth/native-token`, the token response (`access`,
`renewable_until`) and the three new settings.

**Unblocks.**
- The background service: it can keep reading for up to 30 days per pairing.
- It renews before `expires_at`, stores the rotated token, and asks for a new
  pairing once `renewable_until` has passed.
