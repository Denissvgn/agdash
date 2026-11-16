# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

A developer who runs several coding agents (Claude Code, Codex, Antigravity, Pi)
on their own machine, often in parallel. They need to see at a glance what each
session is doing now, and to review afterwards what a session was asked, how it
reasoned, which tools it used and what it consumed.

## Product Purpose

agdash shows every agentic coding session on the host live and keeps its history
for review and analysis: the initial task, model and effort, auth method,
thoughts, tool calls and usage. It maps sessions to the WorkChord projects,
agents and tasks they work under. Success is that the person can answer "what is
running, for what, and how did it go" without opening any harness.

## Positioning

agdash only observes. It never starts, stops or changes an agent, never writes to
a harness's files and never reads credentials. Everything it shows comes from the
process table, open files and the files harnesses already write, and every state
or mapping states the evidence it rests on. Without evidence it says "unknown"
rather than guessing.

## Operating Context

Runs on the developer's own macOS or Ubuntu machine as a desktop app (a window
and a tray item) over a background service. On a headless Ubuntu host the same
interface is reached in a browser through an SSH tunnel; the service listens on
loopback only. It sits beside the terminals, IDEs and agent apps it observes, and
beside WorkChord, whose design system it uses.

## Capabilities and Constraints

- Harnesses: Claude Code (CLI, desktop, IDE), Codex (CLI, exec, desktop),
  Antigravity (app and CLI), Pi.
- Session states: active, waiting, idle, ended, unknown, each with its basis.
- Thoughts are shown at the coverage the harness left: full, summary, encrypted
  or absent. The interface never implies more than the harness wrote.
- Interface language: English only (owner decision, 2026-11-15). Every
  visible string comes from the English message catalogue.
- Session transcripts are private and stay on this host; export is an explicit
  user action.

## Brand Commitments

The visual system is WorkChord's DESIGN.md, used unchanged for everything it
defines, so agdash reads as part of the same toolset. For charts and diagrams,
which it does not cover, agdash adds a figure layer built from WorkChord's own
colours at fixed opacities, with no new hues (DESIGN.md, Figures).

## Evidence on Hand

No real session data may appear in the repository, screenshots or fixtures:
transcripts are private. Demonstration content must be synthetic and labelled as
such.

## Product Principles

- Observe, never act.
- Show the evidence behind every state and mapping.
- Unknown is an honest answer; never present a guess as a fact.
- Private by default: nothing leaves the machine unless the person exports it.
