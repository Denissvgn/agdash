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

agdash uses WorkChord's design system unchanged. WorkChord's DESIGN.md is the upstream authority for every token, rule and component pattern in this file: the colours, type ramp, radii, spacing, shadows, layout measurements and component tokens above are copied from it verbatim, and where this file and WorkChord's design system disagree, WorkChord's wins. agdash adds only the six derived colour roles and the one state duration listed under Colors and Components; each fills a role WorkChord names but gives no value for. The upstream token file in the frontend is generated from WorkChord's frontmatter, never edited by hand.

Within that system agdash is a calm workspace that carries an observer, not a control room. A canvas-mist ground holds working-paper surfaces; a 52px conductor-navy bar owns workspace switching; the cobalt planning thread marks only where the person is. State and its evidence come first, and where nothing is known yet the page says so in words instead of showing an empty frame or a placeholder metric. Nothing in the shell may suggest that agdash can act on an agent.

**Key Characteristics:**

- WorkChord tokens unchanged; six fixed-opacity derived roles, each traced to a named gap.
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

- **Active Cyan, Resolved Emerald, Closed Violet, Planned Slate:** WorkChord's canonical record states. In the shell as built, only Planned Slate appears: it colours the dashed-circle icon of the unavailable-state panel, because a view that waits on its data has not begun.
- **Feedback Warning / Danger:** Reserved for actionable exceptions; not yet used by the shell.

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

**The Closed Palette Rule.** The frontend's colour scale contains only the upstream tokens and the six derived roles; Tailwind's default palette is removed, so an off-palette colour cannot be written by accident. A new derived role needs a cited upstream gap.

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

**The Wordmark Rule.** agdash has no product mark; the name is set in Title weight in working paper on the conductor bar, and links home.

## Layout

The shell is a column: a sticky 52px conductor bar (plus the top safe-area inset), then a row of a 220px sidebar and a fluid main column. The page column is centred and capped at 1240px, with 24px vertical padding and 32px horizontal gutters, reducing to 16px at 520px and below. Horizontal gutters never fall inside the safe-area insets. The conductor bar pads 24px horizontally on wide windows and 16px otherwise.

Inside a page, sections stack on the 20px page gap; a title and its purpose line sit 4px apart. The sidebar pads 8px across and 24px down, and its destination list stays pinned below the bar as the page scrolls. Rows in the sidebar and drawer are separated by 2px.

At 1180px and below the sidebar and the workspace switcher leave the layout entirely and move into the navigation drawer, opened by a labelled Menu button on the left of the bar. The workspace taxonomy does not change between the two layouts. At 520px and below the unavailable-state panel's two-column term grid becomes a single column.

**The One Taxonomy Rule.** Five workspaces (Live, History, Projects, Analytics, Settings) in that order, each with its own destinations; a workspace's first destination lives at the workspace's own address. Wide and narrow layouts show the same structure.

## Elevation & Depth

Depth is structural and low-amplitude, exactly as upstream. Borders (the hairline role) and tonal layering (working paper over canvas mist) carry most separation. Three shadows exist, with the upstream values: Resting on the unavailable-state panel; Raised for deliberately raised hover or focused work (not yet used by the shell); Popover on the navigation drawer panel and the skip link when it appears. Open overlays sit over the scrim.

### Named Rules

**The Structural-First Rule.** (Upstream.) Start with a surface tone or a hairline; add a shadow only when the element actually changes layer.

## Shapes

Gently compact corners from the upstream scale: 6px for every control and navigation row, 8px for the unavailable-state panel and other standard surfaces, 4px for the wordmark's focus outline. Pills are reserved for small status markers; the read-only marker is the only pill in the shell. Structural edges are 1px hairlines. The only coloured edge accents are the planning thread: a 2px inset underline on the active workspace and a 1px inset left edge on the selected sidebar destination.

## Components

### Conductor bar and workspace switcher

The global workspace layer, in conductor navy with working-paper text.

- **Order:** Menu button (narrow windows only), the agdash wordmark, the five workspaces, and the read-only marker pushed to the right edge.
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

### Navigation drawer

The narrow-window home of both the workspace switcher and the current workspace's destinations.

- **Structure:** A native modal dialog covering the window. A working-paper panel on the left, 320px wide or 86% of the viewport, whichever is smaller, with the popover shadow and safe-area padding. The rest of the window is the scrim.
- **Header:** 52px tall with a hairline below: the wordmark in Title size and a 34px square close button (44px on coarse pointers).
- **Content:** The workspace list (34px rows, icon and label; selected rows use the soft tint and cobalt text) and, when the workspace has more than one destination, its sidebar destinations below, 24px apart.
- **Behaviour:** Opened from the Menu button, which reports its expanded state and the dialog it controls. Focus is held inside while open and lands first on the close button. Escape, the close button, a tap on the scrim, choosing a destination or any change of address closes it, and focus returns to the Menu button.

### Primary action

Used once per page or protected decision area, per upstream: primary-ink fill, working-paper text, 34px tall (44px on coarse pointers), 12px side padding, 6px corners, an optional trailing 16px icon. Hover shifts the fill to conductor panel. In the shell it appears only on the page-not-found view, leading back to the live board.

### Skip link

The first focusable element on every page. Hidden until focused, then fixed near the top-left as a working-paper chip with popover shadow, leading to the main content region.

### States and motion

- **State transitions:** Colour changes on every interactive element take the agdash state duration (160ms, a derived value filling the upstream "Motion is brief and explanatory" with no duration given).
- **Drawer:** Slides in over 180ms from 16px left, starting at 85% opacity so a delayed or paused animation never hides it, easing `cubic-bezier(0.16, 1, 0.3, 1)`. There is no exit animation.
- **Reduced motion:** All animations and transitions collapse to an instant switch; the drawer appears without sliding. State feedback (fills, underline, thread) is unaffected.
- **Hover:** Hover styles apply only on devices that can hover, so a tap never leaves one stuck. No information or action depends on hover.

### Accessibility behaviour

- **Focus:** A 2px planning-thread outline offset by 2px on every keyboard-focused element.
- **Targets:** Controls and rows grow to 44px on coarse pointers.
- **Landmarks and names:** A labelled Workspaces navigation, a "<workspace> sections" navigation, a main region that receives the skip link, and pages built as an article titled by their one Display heading. The current workspace and destination carry `aria-current="page"`.
- **Window title:** "<page title> · agdash", including on the not-found page.
- **Icons:** Outline icons are decorative and hidden from assistive technology; every control has a visible or catalogue-sourced accessible name.
- **Viewport:** Full-height layers use dynamic viewport units and respect safe-area insets.

### Message catalogue

Every visible string and every accessible name (labels, titles, tooltips, placeholders) comes from the English message catalogue. Components hold keys, never literal copy; workspace and destination labels, titles, purposes and unavailable-state text live under each workspace's entry. A test renders every route with a marked pseudo-locale and fails on any text that did not come from the catalogue. English is the only language.

## Do's and Don'ts

### Do:

- **Do** treat WorkChord's design system as the upstream authority, and regenerate the token file from it rather than editing values here.
- **Do** cite the upstream gap for any new derived role, and build it as an upstream colour at a fixed opacity.
- **Do** mark the current workspace with the conductor-panel fill and 2px cobalt underline, and the current destination with the soft tint and 1px cobalt thread.
- **Do** keep the read-only marker visible on every page.
- **Do** answer missing data with an unavailable-state panel naming what will appear and what it waits on.
- **Do** take every visible string and accessible name from the English message catalogue.
- **Do** keep 44px coarse-pointer targets, the 2px cobalt focus outline and the reduced-motion instant switch.

### Don't:

- **Don't** add colours outside the upstream tokens and the six derived roles.
- **Don't** build metric tiles, gauges or placeholder numbers where real data is absent.
- **Don't** add controls, wording or affordances that suggest agdash can start, stop, message or change an agent.
- **Don't** reuse cobalt for record status, success, warning or danger.
- **Don't** add continuous motion or motion longer than the brief state and drawer transitions.
- **Don't** write literal copy in components.
