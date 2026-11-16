/**
 * The state-strip encoding (DESIGN.md, Figures: Roles, State). The five session
 * states differ by form, not only colour: waiting, idle and unknown share planned
 * slate, so their height and texture tell them apart, and every mark carries the
 * state's word.
 */

export type StripState = 'active' | 'waiting' | 'idle' | 'unknown' | 'ended'
export const STRIP_STATES: StripState[] = ['active', 'waiting', 'idle', 'unknown', 'ended']

/** The ended cap's width, in pixels. */
export const CAP = 2

export type StateForm = {
  /** The share of the strip's height the block fills, from its baseline. */
  share: number
  fill: string
  hatched: boolean
  /** Drawn as a cap of this width at the moment the state began, rather than a block. */
  cap?: number
}

export const STATE_FORMS: Record<StripState, StateForm> = {
  active: { share: 1, fill: 'fill-status-active', hatched: false },
  waiting: { share: 2 / 3, fill: 'fill-status-planned', hatched: false },
  idle: { share: 1 / 3, fill: 'fill-status-planned', hatched: false },
  unknown: { share: 1, fill: 'fill-status-planned', hatched: true },
  ended: { share: 1, fill: 'fill-status-closed', hatched: false, cap: CAP },
}

/** The block for a state in a strip ``height`` tall with its baseline at ``base``. */
export function stateBlock(state: StripState, x0: number, x1: number, base: number, height: number) {
  const form = STATE_FORMS[state]
  const tall = Math.round(height * form.share)
  return {
    x: x0,
    y: base - tall,
    width: form.cap ?? Math.max(1, x1 - x0),
    height: tall,
    form,
  }
}
