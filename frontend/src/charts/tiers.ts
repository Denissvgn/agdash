/**
 * How sure a link is, drawn in the line itself, strongest first: a recorded tool
 * call is solid, a trace id long dashes, an MCP configuration short dashes, and a
 * heuristic dots. Every edge also carries its tier's word.
 */
export type EvidenceTier = 'tool_call' | 'trace_id' | 'mcp_config' | 'heuristic'
export const EVIDENCE_TIERS: EvidenceTier[] = ['tool_call', 'trace_id', 'mcp_config', 'heuristic']

export const TIER_DASH: Record<EvidenceTier, string | undefined> = {
  tool_call: undefined,
  trace_id: '8 4',
  mcp_config: '3 3',
  heuristic: '0.5 4',
}

/** Dots need round caps; dashes keep square ends. */
export const tierCap = (tier: EvidenceTier) => (tier === 'heuristic' ? 'round' : 'butt')
