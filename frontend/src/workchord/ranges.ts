/** How far back the project activity lanes look, in days; the choice lives in the address. */
export const RANGES = ['7', '30'] as const
export type Range = (typeof RANGES)[number]
