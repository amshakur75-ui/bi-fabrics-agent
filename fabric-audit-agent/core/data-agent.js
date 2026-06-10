/**
 * Build the Fabric Data Agent / MCP manifest from the agent's tool definitions.
 * This is the contract a Fabric Data Agent or MCP server exposes so the auditor is
 * callable from Copilot in Power BI / M365 Copilot / Copilot Studio. Pure: tools in, manifest out.
 * @param {object[]} toolDefinitions  from tools.js `createToolDefinitions()`
 */
export function buildDataAgentManifest(toolDefinitions = []) {
  return {
    name: 'fabric-audit-agent',
    displayName: '[C] Fabric Audit Agent',
    description: 'Read-only Microsoft Fabric / Power BI capacity & performance advisor. Ask it to audit the estate or explain an issue.',
    instructions: 'Call run_audit to sweep the estate and return prioritized findings, a digest, and the capacity verdict. The agent is strictly read-only.',
    readOnly: true,
    tools: toolDefinitions.map(t => ({
      name: t.name,
      description: t.description,
      input_schema: t.input_schema,
    })),
  };
}
