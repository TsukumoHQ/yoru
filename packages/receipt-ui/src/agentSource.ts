const AGENT_SOURCE_LABELS: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex CLI",
  opencode: "OpenCode",
  cursor: "Cursor Agent",
}

export function agentSourceLabel(agent?: string | null): string {
  if (!agent) return "Claude Code"
  return AGENT_SOURCE_LABELS[agent] ?? agent
}
