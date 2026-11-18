---
name: agdash
description: A read-only dashboard for agentic coding sessions, set in WorkChord's design system.
colors:
  planning-thread: "rgb(37 99 235)"
  planning-thread-hover: "rgb(29 78 216)"
  planning-thread-soft: "rgb(239 246 255)"
  conductor-navy: "rgb(14 30 64)"
  conductor-panel: "rgb(24 48 91)"
  canvas-mist: "rgb(248 250 252)"
  working-paper: "rgb(255 255 255)"
  primary-ink: "rgb(15 23 42)"
  secondary-ink: "rgb(71 85 105)"
  strong-rule: "rgb(100 116 139)"
  status-planned: "rgb(100 116 139)"
  status-active: "rgb(8 145 178)"
  status-resolved: "rgb(16 185 129)"
  status-closed: "rgb(168 85 247)"
  feedback-warning: "rgb(217 119 6)"
  feedback-danger: "rgb(220 38 38)"
  # agdash derived roles: upstream colours at a fixed opacity (see Colors).
  hairline: "rgb(100 116 139 / 0.2)"
  inverse-muted: "rgb(255 255 255 / 0.75)"
  inverse-soft: "rgb(255 255 255 / 0.85)"
  inverse-rule: "rgb(255 255 255 / 0.2)"
  conductor-hover: "rgb(24 48 91 / 0.6)"
  scrim: "rgb(15 23 42 / 0.3)"
  # agdash figure layer: conductor navy over working paper at fixed opacities (see Figures).
  figure-ink-1: "rgb(236 237 240)"
  figure-ink-2: "rgb(216 219 224)"
  figure-ink-3: "rgb(188 192 202)"
  figure-ink-4: "rgb(154 161 175)"
  figure-ink-5: "rgb(118 127 146)"
  figure-ink-6: "rgb(72 84 110)"
  figure-ink-7: "rgb(14 30 64)"
typography:
  display:
    fontFamily: "Inter Variable, Inter, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.18
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Inter Variable, Inter, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.18
  title:
    fontFamily: "Inter Variable, Inter, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.25
  body:
    fontFamily: "Inter Variable, Inter, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Inter Variable, Inter, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.25
rounded:
  compact: "4px"
  control: "6px"
  surface: "8px"
  panel: "10px"
  feature: "14px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  page-gap: "20px"
  lg: "24px"
  page-x: "32px"
  xl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.primary-ink}"
    textColor: "{colors.working-paper}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "34px"
  button-secondary:
    backgroundColor: "{colors.working-paper}"
    textColor: "{colors.primary-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "34px"
  input:
    backgroundColor: "{colors.working-paper}"
    textColor: "{colors.primary-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "34px"
  card:
    backgroundColor: "{colors.working-paper}"
    textColor: "{colors.primary-ink}"
    rounded: "{rounded.surface}"
    padding: "20px"
  workspace-active:
    backgroundColor: "{colors.conductor-panel}"
    textColor: "{colors.working-paper}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "6px 10px"
    height: "36px"
  workspace-inactive:
    textColor: "{colors.inverse-muted}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "36px"
  workspace-inactive-hover:
    backgroundColor: "{colors.conductor-hover}"
    textColor: "{colors.working-paper}"
  destination-active:
    backgroundColor: "{colors.planning-thread-soft}"
    textColor: "{colors.planning-thread}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 8px"
    height: "34px"
  destination-inactive:
    textColor: "{colors.secondary-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 8px"
    height: "34px"
  destination-inactive-hover:
    backgroundColor: "{colors.canvas-mist}"
    textColor: "{colors.primary-ink}"
  read-only-marker:
    textColor: "{colors.inverse-soft}"
    typography: "{typography.label}"
    padding: "3px 10px"
  unavailable-panel:
    backgroundColor: "{colors.working-paper}"
    textColor: "{colors.primary-ink}"
    rounded: "{rounded.surface}"
    padding: "20px"
---

# Design System: agdash

## Overview

**Creative North Star: "The Planning Thread, Observing"**

agdash uses WorkChord's design system unchanged. WorkChord's DESIGN.md is the upstream authority for every token, rule and component pattern in this file: the colours, type ramp, radii, spacing, shadows, layout measurements and component tokens above are copied from it verbatim, and where this file and WorkChord's design system disagree, WorkChord's wins. agdash adds only the six derived colour roles and the one state duration listed under Colors and Components, each filling a role WorkChord names but gives no value for, and the figure layer for charts and diagrams, which WorkChord's design system does not cover. The figure layer is built the same way, from WorkChord's own colours at fixed opacities, and adds no hue (see Figures). The upstream token file in the frontend is generated from WorkChord's frontmatter, never edited by hand.

Within that system agdash is a calm workspace that carries an observer, not a control room. A canvas-mist ground holds working-paper surfaces; a 52px conductor-navy bar owns workspace switching; the cobalt planning thread marks only where the person is. State and its evidence come first, and where nothing is known yet the page says so in words instead of showing an empty frame or a placeholder metric. Nothing in the shell may suggest that agdash can act on an agent.

**Key Characteristics:**

- WorkChord tokens unchanged; six fixed-opacity derived roles, each traced to a named gap.
- Figures drawn in one navy ink ramp, with cobalt for selection and the status colours for state only; no new hues.
- Inter Variable at a 14px operational base; hierarchy by weight, not by typeface.
- A conductor bar of five workspaces, a 220px contextual sidebar, a page column capped at 1240px.
- A labelled, focus-managed navigation drawer at 1180px and below.
- A visible, permanent "Read-only" marker on the conductor bar.
- Honest unavailable-state panels instead of empty frames or invented numbers.
- Every visible string and accessible name comes from the English message catalogue.

## Colors

Cool paper neutrals, a deliberately scarce cobalt planning thread, and WorkChord's fixed semantic status vocabulary, all as defined upstream.

### Primary

- **Planning Thread:** The active workspace underline, the selected sidebar row (soft tint, cobalt text and the 1px thread), focus rings, text selection tint and the text caret.
- **Conductor Navy:** The conductor bar only. **Conductor Panel** fills the active workspace and is the hover fill for controls on the bar and for the primary-ink action.

### Secondary

- **Active Cyan, Resolved Emerald, Closed Violet, Planned Slate:** WorkChord's canonical record states. Planned Slate colours the dashed-circle icon of unavailable and empty notices, because a view that waits on its data has not begun. Session state chips map onto the four: active is cyan; ended is violet (closed, with no claim of success); waiting, idle and unknown are slate, since nothing is in progress that agdash can vouch for. The chip always carries the state's word, so colour is never the only signal. A source's chip on Settings · Sources takes the same two: cyan while agdash reads the harness, slate when its folder is not on this machine.
- **Feedback Warning:** The icon of an error notice (the service could not be reached or refused the request), an actionable exception with a retry. **Danger** is the icon of the one question before something irreversible: removing sessions on Settings · Data.

### Neutral

- **Canvas Mist:** The application ground and the hover fill for light-surface navigation rows.
- **Working Paper:** The sidebar, the drawer panel, the unavailable-state panel, and inverse text on the conductor bar.
- **Primary Ink:** Page titles and main text; the fill of the single primary action.
- **Secondary Ink:** Page purpose lines, field terms, inactive sidebar rows and group headings.
- **Strong Rule:** The base of the hairline role and, at 45% opacity, the scrollbar thumb.

### Derived roles (agdash additions)

Each is an upstream colour at a fixed opacity. They exist because WorkChord's design system names the role but states no value. No other colour may be added.

- **Hairline** (strong-rule at 20%): every 1px structural border: the sidebar edge, the drawer header rule and the unavailable-state panel. Fills Shapes: "Structural containers use 1px semantic borders" with no border token given; strong-rule at full strength is kept for high-information input borders.
- **Inverse Muted** (working-paper at 75%): inactive workspace labels on the conductor bar. Fills Navigation: no inverse secondary text token.
- **Inverse Soft** (working-paper at 85%): the menu button and the read-only marker on the conductor bar. Fills Navigation: secondary controls and status on the bar.
- **Inverse Rule** (working-paper at 20%): the border of the read-only marker pill. Fills Chips: "Small bordered pills" on a dark bar, with no inverse border token.
- **Conductor Hover** (conductor-panel at 60%): hover fill behind inactive workspaces. Fills Buttons: "Fast background and border shifts" over the conductor bar.
- **Scrim** (primary-ink at 30%): the layer behind the open navigation drawer. Fills Elevation & Depth: drawers and temporary overlays, with no scrim token.

### Named Rules

**The Planning Thread Rule.** (Upstream.) Cobalt marks location, selection and focus; it never stands in for record status or feedback.

**The Canonical Status Rule.** (Upstream.) Planned is slate, active is cyan, resolved is emerald, closed is violet, everywhere.

**The Closed Palette Rule.** The frontend's colour scale contains only the upstream tokens, the six derived roles and the figure layer's ink steps and roles; Tailwind's default palette is removed, so an off-palette colour cannot be written by accident. A new derived role needs a cited upstream gap; a new figure colour must be an upstream colour at a fixed opacity and pass the figure checks.

## Typography

**Display Font:** Inter Variable (with Inter and system UI fallbacks), self-hosted
**Body Font:** Inter Variable (same family)
**Label/Mono Font:** Platform monospace, only for code, identifiers and measured data

**Character:** One compact, neutral family; weight and restrained scale steps carry the hierarchy. Inter's alternate one and open digits are enabled globally (`cv11`, `ss01`). The document root keeps the browser's 16px so the rem-based token sizes mean what WorkChord states.

### Hierarchy

- **Display** (600, 1.5rem, 1.18, -0.02em): page titles, one per page.
- **Headline** (600, 1.25rem, 1.18): planning-step and prominent section titles; not yet used by the shell.
- **Title** (600, 1rem, 1.25): the agdash wordmark, the drawer header and the unavailable-state panel heading.
- **Body** (400, 0.875rem, 1.5): navigation rows, workspace items, purpose lines and values. Purpose lines and panel content are capped at 70 characters.
- **Label** (500, 0.8125rem, 1.25): the read-only marker and the terms in the unavailable-state panel.
- **Metadata** (0.75rem, 1.25): sidebar group headings, set in 500 weight and secondary ink, sentence case. **Counter** (0.6875rem) is reserved for axes and counters.

Tabular numerals are applied to measured data and identifiers so they align in columns.

### Named Rules

**The Operational Base Rule.** (Upstream.) Interactive and reading text starts at 14px; smaller sizes support the work and never carry the primary instruction.

**The Wordmark Rule.** The name is set in Title weight in working paper on the conductor bar, with the mark before it, and links home.

**The Mark Rule.** The mark is the heart of the ecosystem's root icon: a glowing pentagon and the five cells that share its edges, inside an open white ring. Three cells are lit, two green and one red, as a board of sessions is. It is a picture, not a reading: the lit cells never follow the sessions on the page, and its green and red say nothing of state. Its green, red and charcoal belong to the mark alone and are not interface colours. It always sits on its own near-black tile, 24px beside the name, and never stands in for the name. The app icon uses the full drawing (`desktop/icons/icon.svg`); 32px and under, the page icon and the mark beside the name use the small one (`desktop/icons/icon-small.svg`), with heavier parts and no cell edges.

## Layout

The shell is a column: a sticky 52px conductor bar (plus the top safe-area inset), then a row of a 220px sidebar and a fluid main column. The page column is centred and capped at 1240px (a session's page, a data-heavy workbench, may reach the 1440px upstream allows), with 24px vertical padding and 32px horizontal gutters, reducing to 16px at 520px and below. Horizontal gutters never fall inside the safe-area insets. The conductor bar pads 24px horizontally on wide windows and 16px otherwise.

Inside a page, sections stack on the 20px page gap; a title and its purpose line sit 4px apart. The sidebar pads 8px across and 24px down, and its destination list stays pinned below the bar as the page scrolls. Rows in the sidebar and drawer are separated by 2px.

At 1180px and below the sidebar and the workspace switcher leave the layout entirely and move into the navigation drawer, opened by a labelled Menu button on the left of the bar. The workspace taxonomy does not change between the two layouts. At 520px and below the unavailable-state panel's two-column term grid becomes a single column.

**The One Taxonomy Rule.** Five workspaces (Live, History, Projects, Analytics, Settings) in that order, each with its own destinations; a workspace's first destination lives at the workspace's own address. Wide and narrow layouts show the same structure. A destination stays marked in the sidebar while a page under it is open: History's Sessions while one session's page is, with `aria-current="true"` rather than `page`, since it is the place and not the page itself.

## Elevation & Depth

Depth is structural and low-amplitude, exactly as upstream. Borders (the hairline role) and tonal layering (working paper over canvas mist) carry most separation. Three shadows exist, with the upstream values: Resting on the unavailable-state panel; Raised for deliberately raised hover or focused work (not yet used by the shell); Popover on the navigation drawer panel and the skip link when it appears. Open overlays sit over the scrim.

### Named Rules

**The Structural-First Rule.** (Upstream.) Start with a surface tone or a hairline; add a shadow only when the element actually changes layer.

## Shapes

Gently compact corners from the upstream scale: 6px for every control and navigation row, 8px for the unavailable-state panel and other standard surfaces, 4px for the wordmark's focus outline. Pills are reserved for small status markers; the read-only marker is the only pill in the shell. Structural edges are 1px hairlines. The only coloured edge accents are the planning thread: a 2px inset underline on the active workspace and a 1px inset left edge on the selected sidebar destination.

## Components

### Conductor bar and workspace switcher

The global workspace layer, in conductor navy with working-paper text.

- **Order:** Menu button (narrow windows only), the mark and the agdash wordmark, the five workspaces, and the read-only marker pushed to the right edge.
- **Workspace items:** 36px tall, 6px corners, 10px side padding, a 16px outline icon (1.75 stroke) and a Body label, 4px apart.
- **Active:** Conductor panel fill, working-paper text and a 2px planning-thread inset underline. It stays active on every inner page of its workspace and carries `aria-current`.
- **Inactive / Hover:** Inverse muted text; on hover the conductor hover fill and working-paper text.

### Sidebar destinations

Only the current workspace's destinations, under a Metadata-size group heading in secondary ink.

- **Rows:** 34px tall (44px on coarse pointers), 6px corners, 8px side padding, Body text.
- **Selected:** Planning-thread soft fill, cobalt 500-weight text and a 1px cobalt thread on the row's left edge. This is the upstream "1px cobalt hierarchy thread" made concrete; it marks the current destination and nothing else.
- **Inactive / Hover:** Secondary ink; on hover canvas mist fill and primary ink.
- The same component renders in the drawer, without the sticky behaviour.

### Read-only marker

A quiet, permanent statement that agdash only observes. A pill with a 1px inverse-rule border, 3px by 10px padding, a 14px eye icon and the Label-size text "Read-only" in inverse soft. Its longer explanation ("agdash observes your agents and never changes them.") is always present for assistive technology and is also offered as a pointer tooltip. It is a statement, not a control: it has no hover or pressed state.

### Unavailable-state panel

The honest answer for a view whose data source does not exist yet.

- **Surface:** Working paper, 1px hairline, 8px corners, 20px padding, resting shadow.
- **Heading:** Title size, "Not available yet", led by an 18px dashed-circle icon in planned slate.
- **Content:** A term-and-value list, "What will appear here" and "Waits on", terms in Label size and secondary ink, values in Body and primary ink, capped at 70 characters. Two columns on wider windows, one at 520px and below.
- It never holds placeholder numbers, skeleton tiles or sample data.
- Every destination has its own page now; a destination added before its data exists gets this panel until it does.

### Navigation drawer

The narrow-window home of both the workspace switcher and the current workspace's destinations.

- **Structure:** A native modal dialog covering the window. A working-paper panel on the left, 320px wide or 86% of the viewport, whichever is smaller, with the popover shadow and safe-area padding. The rest of the window is the scrim.
- **Header:** 52px tall with a hairline below: the mark and the wordmark in Title size and a 34px square close button (44px on coarse pointers).
- **Content:** The workspace list (34px rows, icon and label; selected rows use the soft tint and cobalt text) and, when the workspace has more than one destination, its sidebar destinations below, 24px apart.
- **Behaviour:** Opened from the Menu button, which reports its expanded state and the dialog it controls. Focus is held inside while open and lands first on the close button. Escape, the close button, a tap on the scrim, choosing a destination or any change of address closes it, and focus returns to the Menu button.

### Session list

The one dominant work object of the History pages: rows, never cards.

- **Surface:** One working-paper container, 1px hairline, 8px corners, resting shadow; rows separated by hairline dividers, 8px by 16px padding.
- **Row:** two lines. First, a state chip and the session's name (Body, 600, kept at least 12rem wide so the last-active time wraps below it rather than squeezing it; two lines at 520px and below, one truncated line above), then the last-active time, or "No activity recorded". Second, Label-size facts in secondary ink (harness and surface, models, events, subagents, matches) separated by middle dots, ending with the folder in monospace at 12px, as an identifier, truncated from its start so the distinguishing last folders stay visible. Counts use tabular numerals.
- **Name:** a session's title; without one, how its first prompt began (its first line that says something, without markup, cut to 140 characters); only failing both, "Untitled session". Most sessions an agent starts never get a title, and a list of "Untitled session" tells nothing apart. The live board and the session's page use the same name.
- **State chip:** shown where the state says something: active, waiting, idle or ended. "Unknown" on a session long over is true of nearly all history and tells none of it apart, so there the row has no chip. The live board, where unknown is news, always shows it.
- **What is listed:** History's Sessions lists the sessions a person or a schedule started. Sessions another session started are counted on its row ("3 subagents") and reached from its page, and a session known only by name, with no start and no activity, is left out, since it has nothing to open. Search still looks through all of them.
- **Opening:** the name is a link to the session's page, underlined on hover and focus. The live board's rows pick their session for the panel beside them instead (see Live board).
- **Loading:** four still placeholder rows of canvas-mist bars, announced as busy. No shimmer or other motion.
- **More:** a secondary "Show more" button below the list loads the next page; there is no infinite scroll.

### Sessions overview

Above the session list on History · Sessions, a selection window of the sessions started per day, for every current filter but the dates, on the reader's clock, from the first session's day to today. The window shows "Started between": dragging, clicking, shift-clicking or the edge sliders' keys set it, which writes the two dates into the address, so the date fields and the list follow; the date fields stay the authoritative control, and editing them moves the window. Picking every day clears the dates, and Clear filters clears both. A line under the figure names the range ("Selected: Oct 20 to Oct 31"), or "All time: …" when every day is in it, as the empty date fields mean. The plot is 120px tall; on coarse pointers each edge grip has a 44px hit area, and a press outside the window always starts a new range. While the counts load the figure keeps its place; with no session yet it says so. Its table twin lists each day's count and whether it is in the range.

### Session page: related sessions

Under the session's header facts, a navigation region, "Related sessions", of up to two Label-size lines: "Started by" and the name of the session that started this one, as a link; and "Started 12 sessions" followed by the names of those it started, as links, the most recently active first. A session can start hundreds, so at most eight are named and the rest are counted ("and 4 more"). Names follow the session list's Name rule; until they arrive the links stand by number ("Subagent 1").

### Session page overview

Above a session's timeline, one figure, "Overview", shows the whole session on one time axis, its lanes named at the left in Counter size (a 112px column, 84px below 520px): Prompts, Replies, Thoughts, Tool calls, Subagents, Model changes, Compactions, State and Tokens per turn.

- **Counts:** prompts, replies, thoughts and tool calls are columns per bucket (the service's own, at most 120 for the session), each lane on its own scale, in the mark ink. A thought whose reasoning the harness did not keep, and a tool call that failed, are the column's top part in the mark-ink 135° texture over ink step 2, the same texture as the Tools figure's errors.
- **Spans and moments:** subagents are 6px blocks over their lifetime, alternating between two rows so neighbours stay apart; a model change is a dot and a compaction a square, each in its own lane; the state lane is a state strip.
- **Tokens per turn:** a column for each turn from its prompt to the next (stopping where a compressed gap begins), on its own scale, named under the lane's name ("up to 42K"); a turn that reported no usage is hatched, never a zero.
- **Gaps:** stretches with nothing recorded longer than 30 minutes, or three buckets, are squeezed into an 18px break, marked as the time lanes' breaks are.
- **The window:** the part of the timeline in view is planning-thread soft behind the marks, outlined in a 2px planning-thread rule. It follows the list as it scrolls. It is a slider, the figure's second tab stop: the arrow keys move it a bucket at a time (Page Up and Page Down ten, Home and End to the ends), dragging moves it, and a press on the drawing moves it to that moment; each scrolls the list to the first event at or after that time, loading further pages until one is there.
- **Twin and keys:** the bucket marks give their counts in the tooltip and on the keyboard; the table twin has a row per bucket with every lane's count, the tokens of the turns started in it ("not reported" where none reported), and the state at its start.

### Session export

On a session's title line, at its right edge from 521px and on a line of its own below that, a secondary button, "Export", with a 16px download icon and a chevron that turns when open (`aria-expanded`, `aria-controls`). It opens a panel under the title line (working paper, hairline, 8px corners, 16px padding, no shadow): the label "Export this session as" beside three secondary buttons, Markdown, JSONL and Raw lines; a hint in Label size and secondary ink; and a polite status line in primary ink that says the file is being prepared, then its name (in 12px monospace, as an identifier) and size, or why there is none. The buttons are disabled while a file is prepared. A session with no archived lines saves no empty file and says so. In the desktop app the window saves the file with the person's downloads, and saves nothing but a file the page itself made.

### Live board

Every session running now on one screen: slim rows on a shared clock, never tiles, counters or gauges, with one panel beside them for whatever a row has no room for. Sessions appear, change and end in place as the live stream reports them. At 1440 by 900 about twenty sessions show without scrolling.

- **Catching up:** while the service is still catching up on the session files that were there when it started, a Label-size line in primary ink sits under the page header, led by a 14px dashed-circle icon in planned slate: how many files are done of how many (tabular figures, so the line does not shift as they count), that running sessions come first, then the newest, and that lists may be incomplete until it finishes. The line asks the service again every two seconds and is not itself a live region; a visually hidden polite status speaks once when the catching up is noticed and once when it is over. The line leaves when it is over, and the page then fetches its lists again, since what it fetched meanwhile was short. Nothing shows when the service cannot tell. The History sessions page carries the same line.
- **Counts:** one line under the header. The live count in Body and primary ink ("19 sessions live"), then a pill per state with the state's dot and its number ("7 active", "4 waiting"), which are toggle chips: pressed, the board shows only the sessions in the pressed states, each still under the session that started it. The choice lives in the address (`?show=active,waiting`); with nothing in the pressed states a notice says so. The full sentence ("19 sessions live: 7 active · 4 waiting.") is a visually hidden polite status region, and always counts the whole board. While the stream reconnects, a Label line led by a 14px alert-circle icon in feedback warning says the board may be out of date.
- **Two destinations:** Live has two destinations in the sidebar, Board (the rows, at `/live`) and Agent graph (at `/live/agents`), so the graph of which session started which is found by name. It is called a graph of agents, as a session another started is called a subagent; WorkChord's declared agents are named on Projects. They share everything on this page but the drawing.
- **Group by:** a labelled radio group of pills on the counts' line, the label beside its pills: Folder (the default) or Harness (`?group=harness`). It shows while anything is live.
- **Steady order:** nothing a running session does moves its row. Sessions sit in the order they started, the oldest first, each under the session that started it; groups sit in name order, with what is not recorded last. A session's state and its last write show in its chip and its lane, where the row already is. A session that starts is added after the others of its group, so nothing above it moves and what is below moves down by one row. For its first minute it carries a small tag, "New", in Counter size with a planning thread outline and text, beside its title: the word marks it, so nothing has to move or animate to be noticed. The pills in the counts' line are how to see only the active or the waiting. Sorting by state or by the latest write would move rows on nearly every update.
- **Board:** one container (working paper, hairline, 8px corners, resting shadow) holding a header line, the groups, the long-quiet fold and Just ended, parted by hairlines.
- **Columns:** state, session, harness, the last 30 minutes, last active. The header line names them in Counter size and secondary ink, with "30 min ago" and "now" at the ends of the lanes' column; it is for the eye only, since each row says the same in words. The harness column shows from 1340px, the lanes from 521px; below that a row is state, title and time.
- **Row:** one line, 28px, or 24px once the board has more than twenty rows (and 28px again only once it has fewer than sixteen, so a count near twenty does not change every row's height back and forth): the state chip; the title in Label size and semibold, truncated; the harness in secondary ink; the lane; and how long ago the session was last active, short ("now", "4m", "2h", "3d"), with the full words ("Last active 4 minutes ago") visually hidden beside it. The title is a link that covers the row: it picks the session for the panel and puts it in the address (`?session=12`), so a pick can be linked to and Back undoes it. The link's name is the title alone; its state, its time and its lane's words are its description. Up and Down move through the rows in order and take the panel along; Home and End jump to the ends. The picked row wears planning thread soft with a 2px planning thread bar on its left edge and `aria-current`; in forced colours it is outlined in the system's selection colour. On a coarse pointer every row is the touch target's height.
- **Groups:** each group is a 28px heading line on canvas mist over its rows: the folder in 12px monospace cut from its start, or the harness name, in primary ink, then a count in secondary ink with tabular numerals. Grouped by harness, the count is that harness's own sessions, and nested sessions running on another harness are named separately ("4 sessions, 1 started on another harness"). A missing folder or harness reads "Folder not recorded" or "Harness not recorded".
- **Nesting:** a session another one started sits in the rows right under it, its title inset 16px a level behind a small hairline elbow. How the link is known is said in the panel and drawn on Agent graph.
- **Lane:** each live session's last 30 minutes in its row, 208px wide and 18px tall: events per minute above (30 cells of 5px, parted by the 2px gap, on one scale for the whole board, darker for more) and its states below in the state strips' forms. Every lane covers the same 30 minutes, so the rows read against each other. The lane is hidden from assistive technology and the row says the same in words ("Last 30 minutes: 34 events; Waiting for 20 min, then Active for 10 min."); it takes no tab stop and needs no table twin. A session with nothing recorded in the window shows an empty hairline baseline. A key under the board names the events ramp and the forms of the states some lane draws. Lanes are replaced in place when the stream reports a new minute or a session's writes, with no animation.
- **Panel:** beside the board from 1024px (18rem wide, 20rem from 1340px), on working paper with the board's border and shadow, staying in view as the page scrolls. It always shows a session: the one picked, or else the first row, which the steady order keeps the same from one update to the next. From the top: the title in Title size, the state chip with "Last active" and a relative time in words; then labelled facts, each a Label-size label in secondary ink over a Body value, what the session is doing before where it runs: why the state was chosen; the latest prompt, clamped to six lines, with a still canvas-mist bar while it loads; the last 30 minutes at full size (the events strip with its count, the state strip, each on its own line, or "No events in the last 30 minutes", and under them the states in words, "Waiting for 20 min, then Active for 10 min", since no key sits beside the panel); the session that started it, as a link that picks it, with how the link is known ("Started from Claude Code, known from the process tree", or "Known from the harness's record" within one harness); the sessions it started, each a state chip and a link that wraps; harness, surface, latest model and effort; and the folder. Last, a secondary button, "Open session", to the session's page. Below 1024px the panel takes the board's place once a session is picked, under a link back, "All live sessions"; the counts and switches leave with the board, focus moves to the panel's title, and going back returns it to the row.
- **Agent graph:** the second destination, headed "Live agent graph": the same sessions drawn as a graph inside the board's container, in the same groups: a node a session (a hairline box 24px tall holding the state's dot and word in secondary ink and the title in semibold; a dot and word, not a chip, so there is no pill inside a box), with a line from each session to those it started, solid where the harness recorded the link and dashed where it is known from the process tree, and a key under the graph naming the line styles in use. Sessions that started others stand in the first column, level with the first session they started; the rest stand alone in that column. Where a column would be narrower than 200px the started sessions indent 20px under their parent instead, joined by an elbow. A node is the same link as a row, in the same order, with the same keys and the same pick; it adds, for assistive technology, who started it and how that is known. The Board is this drawing's table, so it has no other.
- **Long quiet:** sessions left open with nothing written for a day, none of them working or waiting, are folded into one line under the groups, a native disclosure on a group heading's line, led by a 14px chevron that turns down when open, reading "3 sessions open but quiet for over a day", closed by default; opened, it lists them as rows. They still count as live.
- **Just ended:** a last group, headed "Just ended" with its count, of the sessions that ended in the last 15 minutes, as rows with the Ended chip and when they ended.
- **About this board:** a native disclosure under the board, closed by default, holding what used to sit above the rows: the stream's status line, how to read a lane, that sessions in an unknown state show only while active in the last 30 minutes, what long quiet means, and how long ended sessions stay.
- **Times:** relative, refreshed every 30 seconds with no animation: short in the rows, in words from the browser's relative-time formatting ("now", "5 minutes ago") in the panel and for assistive technology.
- **Stream:** 40 seconds of silence counts as a dropped connection; reconnects wait longer each time, up to 30 seconds, and every reconnect fetches the board again, so a board left showing an error recovers by itself.
- **Loading, empty and error:** still placeholder rows; an empty notice, "Nothing is running", with no board, panel or switches; an error notice with the board's own copy.

### Projects lists

- **Things that need a look:** the attention panel on Projects · Board counts every signal in its heading, and lists each kind once: signals that say the same of several sessions are one line ("No task: 9 sessions work in a mapped project but are linked to no task:") followed by a link a session, so nine sessions with no task are one sentence and not nine. A signal about one run or one session keeps its own line.
- **Unmapped queue:** one row a session. From 1024px a row is a line: on the left the state chip, the name and the last-active time, and under them the harness and the folder, parted by a middle dot; on the right the project select and "Pin", side by side at every width. The select's label is visually hidden, since its first line says what to choose. The page's own purpose line says why sessions are here, so a row adds only evidence that disagrees, listed under the left side. Below 1024px the select and button drop under the facts.

### Projects figures

- **Activity by project:** on Projects · Board, under the attention list, a time-lanes figure with a lane per project (by name, sessions mapped to none last) and each session active in the range as a span from its start to its last activity, the "now" line in the planning thread. A Range switch above it picks the last 7 or 30 days; it lives in the address (`days=30`; 7, the default, leaves it). Over 7 days, idle stretches of over 2 hours are compressed into breaks, so several days still fit; over 30 days the axis stays linear with a label each week, since a break for every night would leave no room to read it. Wherever breaks are used they take at most a third of the axis: past that, the shortest gaps stay to scale. A time label names its day whenever the day differs from the label before it, and labels that would touch are dropped. The now line sits under the marks, so a session live now keeps its ring on it. Sessions are placed by the same resolver as the board, so a conflict or no evidence leaves a session in the unmapped lane.
- **Declared and observed:** each mapped project's group leads with a dumbbell, one row per agent: its current assignments in the project (declared, a ring in the mark ink, wider than the dot so that equal values read as a dot inside its ring) against the live sessions mapped to it (observed, a dot one step darker), joined by a 2px context-ink line on a shared count scale that spans at least 0 to 4, so small counts sit near the names. Each row ends with its pair in words ("2 assigned · 3 live"). Focus or hover on a row says how each observed session was mapped ("Its run: 2, Its own WorkChord call: 1"); its table twin lists the same. It replaces the earlier row of agent pills.
- **A session's mapping:** on a session's page, under its header, a tree from the session to its agent, its project and each of its runs with their tasks. Each edge's line follows how the link is known, named in the legend, which shows even when every link is known the same way: a recorded link (a pin, the session's own run or task, or a run's tool call) is solid; a matching record (the agent's own call, its assignments, a runtime rule, a trace id) long dashes; a folder or repository rule, or an MCP configuration, short dashes; and a heuristic run link, or the best guess on conflicting evidence, dots and is labelled "Suggestion", never shown as the mapping. A session nothing maps says "Not mapped" with how to map it; with no WorkChord server connected the figure is left out. A leaf's name is clipped to the room left to the card's edge; the tooltip and table twin carry it in full.

### Settings · Sources

What agdash reads on this machine, as one list of harness records in the session list's row grammar; never tiles and never a form.

- **Summary:** one Body line in primary ink above the list: how many of the harnesses were found and, when it is so, how many may have changed their format, which is the count of the warning lines below and not of every old gap.
- **Record:** a row per harness, in the service's order, inside one container (working paper, hairline, 8px corners, resting shadow), rows parted by hairlines, 12px by 16px padding. First line: a found chip, the harness's name (Body, 600) and, once a file of it has been read, "Last read" with the time at the right. Second line: Label-size facts in secondary ink parted by middle dots: files read, how much of them was read, sessions, the format read most recently, and "Every record understood" when that is so. The format's version is in 12px monospace, as an identifier. A count of zero is left out, never shown as 0; a harness with nothing read says so in words ("No session files read yet", "Nothing of it on this machine"), and one whose folder is gone gives its sessions as "kept from earlier", so "Not found" and a count never seem to disagree.
- **Found chip:** the state chip's form with the word "Found" or "Not found": active cyan while agdash reads the harness, planned slate when its folder is not here.
- **Places:** under the facts, each place the harness is looked for, in 12px monospace as an identifier: primary ink when it exists, secondary ink followed by "not found" when it does not. A settings file says "sign-in method only". A path may break after any slash, and inside a name only when the name alone is wider than the line; the store's path on Settings · Data is set the same way.
- **Not understood:** shown only when true. Something not understood in a format read in the last 14 days is a Label line in primary ink led by a 14px alert-circle icon in feedback warning, saying what, since when, and that some records are not shown; the same in formats read earlier is one Label line in secondary ink. Under either, a native disclosure, "By format", led by a 14px chevron that turns down when open, holds a table of each such format: unknown records, unreadable lines, first read and last read. At 520px and below, where five columns do not fit, each format is its own short list of the same terms and values instead.
- **Notes:** Label lines under the list say what agdash never does in these folders, what it reads of a harness's settings, why a harness kept elsewhere is not found (naming the harnesses' own folder settings in monospace), and what "not understood" means.
- **Catching up, loading and error:** the live board's catching-up line; four still placeholder rows; an error notice with the page's own copy.

### Settings · Data

What agdash keeps, for how long, and how a session leaves it: three titled sections, Store, Retention and Export, in the WorkChord settings' form (working paper, hairline, 8px corners, 20px padding, resting shadow, a Title heading). The page changes two things, both in agdash's own store and neither in a harness: how long history is kept, and whether original lines are. Every control says what it will do before it does it, and nothing is removed without either a saved limit and the day's pass, or a question answered.

- **Terms and values:** the unavailable-state panel's grid: terms in Label size and secondary ink, values in Body and primary ink, on one baseline, capped at 70 characters; one column at 520px and below.
- **Names:** a setting or a command is written as the service takes it, in 13px monospace on canvas mist with 4px corners, with what it does in Label size and secondary ink under it. Commands a person runs themself sit below a hairline, under a Body-size heading in 600 weight.
- **Store:** where the store is (its path in monospace), its size, how many sessions it holds and when the earliest started, and the original lines. Keeping a copy of them is a native checkbox with its label, in the planning thread's accent, saved as it is changed, since it removes nothing: the line under it says what holds now and how much is archived, and a polite status line says it was saved and that the harnesses' own files are not touched. The wording is always of agdash's copy, never of the harness's lines themselves. Where the service was started with the setting, its name is given. The archive's size takes seconds to measure on a large store, so it is asked for apart from the rest and kept for five minutes: its line says "Measuring what is archived." until it arrives, and says so if it could not be measured.
- **Retention limit:** a lead sentence in primary ink gives the limit, or says none is set. Under it the grouping switch, labelled "Keep": Everything, then 30, 90, 180 and 365 days, joined by a limit set elsewhere. Picking another than the saved one opens a protected area under the pills (canvas mist, 1px strong rule, 8px corners, 16px padding, as the WorkChord approval step): what that limit would remove now, under a Label line naming the limit; a Label line that saving removes nothing now, and that from an hour later the service removes what is past the limit once a day without asking again, for good; and the page's primary action, "Save limit", beside a secondary "Cancel". "Save limit" stays disabled until what the limit would remove has been shown; if that cannot be worked out the area says so and offers a secondary "Try again", and the limit cannot be saved meanwhile. A polite status line then says it was saved, that nothing was removed, and when the next daily removal is.
- **Retention facts:** "Set by" (this page, the setting where the service starts, or nothing); with a limit, "Would remove now" with the sessions and the time they were last active before, "Keeps although older" for those that may still run, "Removed" (once a day by the service, with the next time, never sooner than an hour after a limit is saved; or only by the command, where the service does not remove), and "Last removed" (when, how many sessions and events; that nothing was past the limit; or that it did not finish, with the kind of failure), over a Label line that the store's file keeps its size.
- **Remove now:** below a hairline, under a Body-size heading in 600 weight, a secondary button that names the count ("Remove 1,204 sessions now"). It opens the same protected area: the question in Body size and 600 weight, led by a 16px alert-triangle icon in feedback danger; a Label line that the sessions go from agdash with their events, usage and archived lines, cannot be brought back, and that the harnesses' own files are not touched; then the primary action, naming the count again, and a secondary "Keep them", which has the focus when the question opens and returns it to the button that asked. As the question opens the page asks the service again, and the primary action waits for the answer, so the count is of what the store holds now; the time shown goes with the request, and nothing last active after it is removed, however long the page was open. Once it is asked, the focus moves to the "Remove now" heading. While sessions are removed a polite status line says so and the page asks the service again every two seconds; when it is over the facts change in place and every list fetched before it is fetched again. With nothing past the limit, a line says so in the button's place. Where the service does not remove by itself there is no button, only the command.
- **Encoded data:** under the original lines, a second checkbox of the same form, "Keep encoded data in the original lines", off unless set otherwise, with a line saying what is done ("Long runs are left out, with a note of their length.") and a Label line saying what encoded data is and why it is left out. While it is left out and chunks archived before have not been looked at, a line gives their count and a secondary button, "Take it out of what is archived", opens the protected area of Remove now: the question in Body size and 600 weight led by the alert-triangle icon in danger, that it cannot be undone and that the harnesses' own files are not touched, a primary button "Take it out" and a secondary "Leave it", which has the focus. While the service does it a polite status line counts the chunks left, asked for again every two seconds; afterwards a Label line says when it was done and how much smaller the archive is.
- **What the store holds:** inside Store, below a hairline under a Body-size heading in 600 weight, a small ledger of the file's parts, the largest first: the events with their search index and the rest, the original lines, and room free inside the file. Each row has its size and its share of the file, and an 8px share bar under its name, as the Analytics ledgers do. It takes seconds to measure on a large store, so it is asked for apart from the rest and says "Measuring what the store holds." until it arrives; a part that cannot be told is left out, never shown as nothing. A note says what free room inside the file is, and when it goes back to the disk: at the service's next start when a quarter of the file or more is free, or a little at a time after a removal once the store has been compacted.
- **Export:** what each format is, that every export is redacted, a secondary button to History, and the command.

### Settings · Service

What agdash itself costs this machine, from what the service measures of its own process: three titled sections, Now, Over time and By part, in the same form as Settings · Data. The page changes nothing and has no control but its range.

- **Now:** the terms-and-values grid, never tiles or gauges: Processor as a share of one core, with how many cores the machine has under it; Memory as the footprint, with the resident part and why the rest still counts under it; Read from disk and Written to disk, each second; Store, with the room left on its volume; and since when it has measured. A figure the system did not give says "Not reported" in secondary ink. A lead line says when it was measured and that nothing here is about an agent; a copy of the service that does not measure itself says so and shows the last figures kept.
- **Range:** the grouping switch, labelled "Show": the last hour, the last day, the last 90 days. It lives in the address (`range=`), and the hour, the default, leaves it. The hour is drawn in ten-second steps and asked for again every ten seconds; the day in five-minute steps and 90 days in six-hour steps, asked for again each minute. New values replace the old in place.
- **Over time:** five line figures on the range's time axis, each one series in the mark ink with its own value axis in its own unit: Processor (% of one core), Memory, Read from disk, Written to disk, Store size. Read and written are apart because what is written is a small fraction of what is read. A stretch in which the service was not running is hatched as not reported, never drawn as zero. Each has its table twin.
- **By part:** ranked bars of the processor time each part of the service took over the range, as a share of one core: reading harness files, watching the process table, waiting for file changes, the WorkChord mirror, removing old sessions, answering pages, measuring itself, and everything else. Parts are told apart by their row labels.
- **Loading and error:** four still placeholder rows; an error notice with the page's own copy.

### State chip

A 22px pill with a 1px hairline border, a 6px canonical status dot and the state's word in Label size and secondary ink.

### Filter bar

Secondary controls above the session list, per WorkChord's Persistent Object Rule.

- **One line:** the harness pills, the period and "More filters" sit on one line above the list, wrapping where the window is narrow, so the list starts close under the page header. What is asked for most is in view; the rest is one press away at every width.
- **Harness pills:** 30px bordered pills (44px on coarse pointers) with the harness name and its session count in tabular numerals. They are toggle buttons (`aria-pressed`); a pressed pill takes the planning-thread soft fill, cobalt border and cobalt text, as a selection.
- **Period:** a labelled radio group, "Started", of pills for the last 7, 30 and 90 days and All time, styled as the grouping switch. A preset follows today, so the address keeps the preset (`range=7`) rather than its dates. All time is checked when no date is set. History and Analytics share it.
- **More filters:** a ghost button with a chevron that turns when open (`aria-expanded`, `aria-controls`), opening a panel under the line: model, folder and sign-in method selects and two date inputs, each with a visible Label-size label above it. The dates are one fieldset, "Started between", side by side at every width; they show the days a preset stands for, and editing one turns the preset into those fixed days. The panel is open on arrival when one of its filters is set; after that the button always toggles it, and says how many filters are set behind it whether it is open or closed ("More filters (2 set)"; the two dates count as one). A range picked on a figure sets the dates without opening the panel. A select with nothing to choose is disabled and says why in a helper line.
- **Clear filters:** a ghost button, shown only while a filter is set.
- **Address:** every filter lives in the page address, so a filtered list reloads, bookmarks and goes back as it was.

### Search

- **Query:** a labelled search field and the page's one primary action, "Search". The query lives in the address.
- **Results:** the session list, each row adding its number of matches and its two best excerpts. An excerpt starts with the kind of record it came from (Prompt, Reply, Thought, Tool output…) in Label size, then the text in Body.
- **Matches:** marked with the planning-thread soft tint behind primary ink, 4px corners, as the text-selection tint is: a match marks where the reader's words are, which is location, not status. The tint alone is too faint against working paper, so the match is also set in 600 weight.
- **Counts:** the harness pills on the search page carry no session counts, because those would describe all sessions rather than the matches.
- **Announcements:** one persistent status region announces the outcome of a search (results, nothing found, or a failure).

### Notices

Empty and error states share one quiet panel: working paper, hairline, 8px corners, 20px padding, resting shadow; a Title-size heading led by an 18px icon (dashed circle in planned slate for an empty state, alert circle in feedback warning for an error) and one Body line in secondary ink saying what is true and what to do next. An error notice adds a secondary "Try again" button. Its copy comes from the catalogue entry of the page it sits on, so each page explains the failure in its own terms.

A service that answers "preparing its store" is not a failure, so it gets the quiet form, not the error form: the dashed circle, the same title and body on every page ("The service is preparing its store", why that takes minutes, and that the page loads by itself), and a secondary "Check now" button. A figure whose values did not come for that reason says the same in its own frame, without a button. Nothing needs to be clicked: the app asks the service every five seconds whether it is ready and then asks for everything again.

### Buttons and fields

- **Secondary:** working paper, 1px hairline border, primary ink; hover canvas mist.
- **Ghost:** no border, secondary ink, 30px; hover canvas mist and primary ink.
- **Disabled:** canvas mist and secondary ink at full opacity (browsers fade disabled controls otherwise), with a not-allowed cursor; it stays labelled.
- **Fields:** working paper, 1px strong-rule border, 6px corners, 34px (44px on coarse pointers), 10px side padding; placeholder in secondary ink; on focus a cobalt border and a 3px planning-thread soft ring.

### Data tables

On the Analytics pages the ledger stays the authoritative object: one table per page, which a figure may lead (see Figures), and never metric tiles or gauges.

- **Usage over time:** Analytics · Usage leads its ledger with one figure: a small multiple per harness, or the top six models or folders plus Other, all on one scale, with columns per hour (a range of two days or less), day (up to about three months) or week. A Measure switch beside "Group by" picks Tokens, Cost or Sessions; it lives in the address (`measure=`), and Tokens, the default, leaves it. The figure asks for the same sessions as the ledger. Its time axis runs over the selected dates, from the first day to the last (or today), so days with no active session show as true zeros; with no dates it runs from the first session to the last. Its caption gives its total and says it is the table's, then how many of the selection's sessions reported the measure, out of every session in it ("Reported by 129 of 174 sessions"; the ledger's "from n of m" under a total counts out of the sessions with usage instead); usage with no time, which the table counts and the figure cannot place, is said in words. Its table twin, under "Show as a table", lists every bucket for every panel. A bucket where sessions were active and none reported the number is hatched; one where only some reported it has a 6px hatched coverage strip under the baseline and "from n of m" in its tooltip and the table twin. A bucket with no active session is a true zero. Grouped by model, sessions are not counted per bucket, so there is no coverage strip and the caption carries the coverage alone; sessions by model are not split at all, and the figure says so rather than drawing them.
- **Tool use:** Analytics · Tools leads its table with ranked bars of calls in the mark ink, the most used first, the twelfth and beyond folded into Other. The calls that failed are the bar's end, parted by the 2px gap and textured with mark-ink lines at 135°, 4px apart, over ink step 2 (so it never reads as the not-reported hatch), and the label after the bar gives the calls and the errors ("40 · 3 errors"). Errors are known only for calls whose results were matched; where none were, the bar is whole and its tooltip says the errors are not known. The table below is the figure's twin, so the figure has no disclosure of its own.
- **Activity:** a third Analytics destination, after Usage and Tools. It shows, for the current filters and in the reader's time zone, an hour-of-week heatmap (Monday first, hours 00 to 23) of session-hours, and the sessions at once as step small multiples, All sessions first and then one per harness, per quarter hour (a range of two days or less), hour (up to a week) or day, over the selected dates; by the day the figure is titled "Sessions active each day", since a day is too long to say "at once". With one harness in the selection its panel would repeat All sessions, so only All sessions is drawn. Each figure's caption says how activity is counted: a session is active in a stretch of time when it recorded at least one event in it, which is what the harness wrote down, not whether anyone was busy. Both have table twins.
- **Share bars:** each ledger row carries an 8px share bar under its name, the row's share of the largest row by the figure's measure, and the rows are listed by that measure, largest first, with the unrecorded row last; hidden from assistive technology since the cells give the numbers; a row that reported nothing is hatched.

- **Surface:** working paper, 1px hairline, 8px corners, resting shadow; it scrolls sideways inside its own box on narrow windows, and the page itself never does.
- **Structure:** a visually hidden caption; column headers in Label size, 500 weight, secondary ink; row headers on the left, never wrapped; hairline row dividers; a totals row under a 1px strong rule, in 600 weight.
- **Numbers:** right-aligned, tabular numerals, grouped digits; 12px cell padding. Cost in US dollars as reported. Durations in hours and minutes.
- **Unknown and partial:** a number the harness did not report is a dash in secondary ink with a visually hidden "not reported", never a zero. A reported zero shows as 0. A sum built from only some of the sessions with usage carries a second line, "from 3 of 15", in 12px secondary ink, and so does a summed time that only some sessions had; a row whose sessions only partly reported usage says so under its session count ("3 with usage"). Notes under the table explain dashes, sessions with no usage, and that elapsed time includes idle time.
- **Units and scrolling:** a "Tokens" header spans the five token columns. The table box is a labelled region that takes keyboard focus so it can be scrolled sideways, and its first column stays in view while it scrolls.
- **Omitted columns:** a column that can never hold a value for the current grouping (time, grouped by model) is left out, and a note says why.

### Grouping switch

"Group by" with the page's groupings (Harness, Model and Folder on Analytics; Folder and Harness on the live board), and the same switch for a measure, a range or a period: a labelled radio group with its label beside the pills, on their line, never above them; native radio buttons, visually hidden, inside pills styled as the filter pills. The checked pill takes the planning-thread soft fill, cobalt border and cobalt text; keyboard focus draws the 2px cobalt outline around the pill. The choice lives in the page address.

### Primary action

Used once per page or protected decision area, per upstream: primary-ink fill, working-paper text, 34px tall (44px on coarse pointers), 12px side padding, 6px corners, an optional 16px icon. Hover shifts the fill to conductor panel. It appears on the page-not-found view, leading back to the live board, as "Search" on the search page, and on Settings · Data as "Save limit" and, inside its question, as the removal.

### Skip link

The first focusable element on every page. Hidden until focused, then fixed near the top-left as a working-paper chip with popover shadow, leading to the main content region.

### States and motion

- **State transitions:** Colour changes on every interactive element take the agdash state duration (160ms, a derived value filling the upstream "Motion is brief and explanatory" with no duration given).
- **Drawer:** Slides in over 180ms from 16px left, starting at 85% opacity so a delayed or paused animation never hides it, easing `cubic-bezier(0.16, 1, 0.3, 1)`. There is no exit animation.
- **Reduced motion:** All animations and transitions collapse to an instant switch; the drawer appears without sliding. State feedback (fills, underline, thread) is unaffected.
- **Live updates:** The live board changes in place without animation: rows appear, change and leave instantly, and relative times refresh every 30 seconds without motion.
- **Hover:** Hover styles apply only on devices that can hover, so a tap never leaves one stuck. No information or action depends on hover.

### Accessibility behaviour

- **Focus:** A 2px planning-thread outline offset by 2px on every keyboard-focused element. Elements that hide the browser's own ring do so only while focused, so in forced colours nothing carries a frame at rest.
- **Forced colours:** Text and controls take the system colours. A figure's marks keep their ink, textures and forms, so identity (position and label) and state (form and texture) read as they do normally. A pressed or checked pill takes the system's selection colours (Highlight and HighlightText), since its usual fill and border would flatten to those of the pills around it.
- **Figures:** Each plot is a labelled group whose marks sit directly inside it, never inside an image, so a screen reader announces the active mark's label. How every figure screen is checked, by script and by hand, is in docs/figures-review.md.
- **Targets:** Controls and rows grow to 44px on coarse pointers.
- **Landmarks and names:** A labelled Workspaces navigation, a "<workspace> sections" navigation, a main region that receives the skip link, and pages built as an article titled by their one Display heading. The current workspace and destination carry `aria-current="page"`.
- **Announcements:** The live board's summary sentence is a polite status region, so changes in the live counts are announced without moving focus.
- **Window title:** "<page title> · agdash", including on the not-found page.
- **Icons:** Outline icons are decorative and hidden from assistive technology; every control has a visible or catalogue-sourced accessible name.
- **Viewport:** Full-height layers use dynamic viewport units and respect safe-area insets.

### Message catalogue

Every visible string and every accessible name (labels, titles, tooltips, placeholders) comes from the English message catalogue. Components hold keys, never literal copy; workspace and destination labels, titles, purposes and unavailable-state text live under each workspace's entry. A test renders every route with a marked pseudo-locale and fails on any text that did not come from the catalogue. The names of settings and commands a page shows are catalogue strings too. English is the only language.

## Figures

Charts and diagrams, which WorkChord's design system does not cover. They follow one rule, **ink first, colour for meaning**: WorkChord's reserved colours (cobalt for location, the four status colours for record state, warning and danger for exceptions) leave no room for a palette of identity hues, so figures add none. Measured in OKLab, a standard chart palette's blue sits ΔE 6.5 from cobalt, its aqua 2.8 from resolved emerald, its orange 5.4 from warning and its red 5.2 from danger, and no set of four distinct hues stays clear of them in both the light theme and WorkChord's dark theme.

### Roles

- **Ink ramp:** seven steps of conductor navy over working paper at fixed opacities (8, 16, 28, 42, 57, 76 and 100%), `figure-ink-1` nearest the surface to `figure-ink-7`. It carries magnitude: bars, columns, areas, heatmap cells and activity strips. Its dark values, working paper over WorkChord's dark card surface (4, 8, 18, 28, 38, 75 and 87%), are set now and switch with the dark theme; they leave out the band around dark cobalt's lightness, and stop short of pure white, which has no hue.
- **Mark** (`figure-mark`, ink step 6) is the default mark; **mark hover** (step 7) lifts a hovered or focused mark; **context** and **Other** (step 3) carry de-emphasised series and folded tails.
- **Identity:** a harness, model, folder or tool is told apart by position and label (lanes, small multiples, row labels), never by a hue of its own. Many entities are ranked bars, or the first six as small multiples with the rest folded into Other.
- **Selection and location:** the planning thread, as everywhere: a selected mark or series, the part of a session in view, the "now" line on a time axis. Keyboard focus keeps the 2px cobalt outline.
- **State:** the canonical status colours, only in state strips and state segments (the live board uses them at 6px in its rows' lanes and at 12px in its panel, see Live board), always with the state's word in the legend or tooltip. Waiting, idle and unknown share planned slate, so they differ by form: active is a full-height block in active cyan, waiting a two-thirds block and idle a one-third block in slate, unknown a full-height slate block hatched, and ended a 2px closed-violet cap.
- **Not reported:** a 45° hatch of 1px strong-rule lines at 35% opacity, 6px apart, with a label; never a zero. A sum built from part of the sessions says "from n of m", as the ledger does.

### Furniture and marks

- Gridlines are the hairline role, solid and 1px; the baseline is strong rule. Axis labels are Counter size (11px, which WorkChord reserves for axes and counters) in secondary ink, with tabular numerals.
- A tooltip is a working-paper panel with the popover shadow and 8px corners: the value first in primary ink, the name after in secondary ink. A crosshair is a strong-rule hairline. Tooltips add to labels and the table, never replace them.
- Bars are at most 24px thick, with a 4px rounded data end and a square base on one baseline. Touching fills are parted by a 2px gap of the surface colour, never a stroke. Lines are 2px with round joins; dots are at least 8px with a 2px surface ring; an area is its line's colour at 10%.
- **Time lanes:** one lane per entity on a shared time axis; a span is a 12px block in the mark ink with 2px corners, a moment a dot. An idle gap longer than the figure's threshold is squeezed into an 18px break, filled canvas mist and marked on the axis by two slanted strong-rule strokes, and its length is in the tooltip and the table. The "now" line is a 2px planning-thread line labelled "Now".
- **State strips:** an 18px strip per session on a hairline baseline, its blocks parted by the 2px gap. The unknown texture is planned-slate lines at 45°, 4px apart, inside a slate outline, so it never reads as the not-reported hatch.
- **Heatmap:** square cells up to 28px, parted by the 2px gap, with 2px corners; on a narrow screen they narrow so every column fits (an hour-of-week grid at 320px has columns of about 9px), never below 6px, while each row keeps at least 14px so the row names stay apart. A reported zero takes ink step 1. Whole counts up to six take a step each, spread over the ramp so the largest is step 7; any other range is cut into six equal bins on steps 2 to 7, up to a top rounded so each bound reads cleanly. A scale legend under the grid names each step's upper bound.
- **Tree:** laid out left to right, nodes as dots with their names. An edge's line says how its link is known: solid for a recorded tool call, 8–4 dashes for a trace id, 3–3 dashes for an MCP configuration, dots for a heuristic, always 2px in the mark ink, with the link's label on the edge where there is room and always in the tooltip and table.
- **Selection window:** the selected range of an overview is planning-thread soft behind the marks, outlined in a 2px planning-thread rule, with a 6px planning-thread grip at each edge; the marks inside are the mark ink and those outside the context ink.

### Behaviour

- **Table twin:** every figure has a table of the same values, so nothing is only drawn. A strip set inside a list row (the live board's lanes, and the strips in its panel) is not a figure of its own: the row says the same in words, so it takes no tab stop and needs no twin.
- **Keyboard:** a figure is one tab stop; the arrow keys move between its marks and show the same tooltip as hover, and every mark has an accessible name from the catalogue.
  In lanes and strips, up and down move to the nearest mark on the row above or below, and Home and End go to the first and last span or moment, the axis breaks following them in order; in a heatmap, to the cell above or below; in a tree, up and down move through the nodes in order, left to the parent and right to the first child.
- **Selection window:** its two edges are sliders, so it has two tab stops (the session overview's window is the other figure with a second one). The arrow keys move an edge by one step, Page Up and Page Down by seven, Home and End to the ends; with Shift the arrow keys move the whole window. The pointer drags an edge or the window; a press outside the window (or anywhere, when it covers every step and so cannot move) starts a new range, which a drag draws; a click picks one step, and a shift-click extends the range to the step clicked.
- **Motion:** none. Figures do not animate in, and new data replaces old in place.

### Checks

The values are generated by `frontend/scripts/figure-colours.mjs` from WorkChord's tokens and tested on every build: each ink step is at least ΔE 18 (OKLab × 100) from cobalt in its mode (19.1 at the closest in light, 18.5 in dark); the steps read in order away from the surface; the steps that carry marks (4 to 7) are at least 0.06 apart in lightness; and each mode keeps one hue. The ordinal checks of the dataviz palette validator pass for steps 4 to 7 on working paper (lightest 2.62:1) and on the dark card surface (2.49:1). Step 5 in light sits ΔE 4.3 from planned slate, which WorkChord also uses as its strong rule; a state is never told by that colour alone, since it always carries a dot and a word.

The chart kit in `frontend/src/charts` draws by these rules, and its geometry tests read the mark values from this section. The development server's `/specimen.html` shows every component in every state from synthetic data; the production build leaves it out.

## Do's and Don'ts

### Do:

- **Do** treat WorkChord's design system as the upstream authority, and regenerate the token file from it rather than editing values here.
- **Do** cite the upstream gap for any new derived role, and build it as an upstream colour at a fixed opacity.
- **Do** mark the current workspace with the conductor-panel fill and 2px cobalt underline, and the current destination with the soft tint and 1px cobalt thread.
- **Do** keep the read-only marker visible on every page.
- **Do** answer missing data with an unavailable-state panel naming what will appear and what it waits on.
- **Do** have a control that changes agdash's own store say what it will do before it does it, and ask before anything that cannot be undone.
- **Do** take every visible string and accessible name from the English message catalogue.
- **Do** keep 44px coarse-pointer targets, the 2px cobalt focus outline and the reduced-motion instant switch.
- **Do** draw figures in the ink ramp, mark selection with cobalt, use the status colours only for state, and give every figure a table twin.

### Don't:

- **Don't** add colours outside the upstream tokens and the six derived roles.
- **Don't** build metric tiles, gauges or placeholder numbers where real data is absent.
- **Don't** give a harness, model, folder or tool a colour of its own, and don't draw a number that was not reported as zero.
- **Don't** add controls, wording or affordances that suggest agdash can start, stop, message or change an agent.
- **Don't** reuse cobalt for record status, success, warning or danger.
- **Don't** add continuous motion or motion longer than the brief state and drawer transitions.
- **Don't** write literal copy in components.
