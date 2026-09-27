/**
 * Yoru plugin for OpenCode
 *
 * This plugin subscribes to OpenCode events and streams them to Yoru backend
 * for session tracking and analysis.
 *
 * NOTE: This plugin is meant to be installed in an OpenCode environment
 * (.opencode/plugins/), not in this repository. The @opencode-ai/plugin
 * dependency is provided by OpenCode's plugin system.
 *
 * TYPECHECKING STATUS: UNVERIFIED
 * This file uses @ts-ignore for the @opencode-ai/plugin import because the package
 * is not available in this repository. Typechecking can only be verified in an actual
 * OpenCode environment by:
 * 1. Copying this file to .opencode/plugins/yoru.ts
 * 2. Adding dependencies to .opencode/package.json
 * 3. Running bun install (OpenCode does this automatically)
 * 4. Running bunx tsc in the .opencode directory
 *
 * For installation:
 * 1. Copy this file to .opencode/plugins/yoru.ts
 * 2. Add dependencies to .opencode/package.json:
 *    {
 *      "dependencies": {
 *        "@opencode-ai/plugin": "latest"
 *      }
 *    }
 * 3. OpenCode will run bun install automatically at startup
 *
 * Note: This plugin uses globalThis.fetch (available in Bun/OpenCode) instead of
 * node-fetch, so no additional fetch dependency is needed.
 */

// @ts-ignore - @opencode-ai/plugin is only available in OpenCode environment; typecheck unverified
import type { Plugin } from "@opencode-ai/plugin";

interface YoruConfig {
  server: string;
  token: string;
}

interface YoruEvent {
  session_id: string;
  kind: string;
  ts: string;
  tool?: string;
  path?: string;
  content?: string;
  cwd?: string;
  git_remote?: string;
  git_branch?: string;
  agent: string;
  raw?: Record<string, unknown>;
}

interface OpenCodePluginContext {
  client?: any;
  directory?: string;
}

interface OpenCodeEventEnvelope {
  event: any;
}

/**
 * Log a message using OpenCode's logging API.
 * Falls back to console if logging fails, ensuring the plugin never blocks.
 */
async function logYoru(
  client: any,
  level: "info" | "warn" | "error",
  message: string,
  extra?: Record<string, unknown>
): Promise<void> {
  try {
    if (client?.app?.log) {
      await client.app.log({
        body: {
          service: "yoru",
          level,
          message,
          ...(extra && { extra }),
        },
      });
    } else {
      // Fallback to console if OpenCode logging unavailable
      const consoleMethod = level === "error" ? console.error : level === "warn" ? console.warn : console.log;
      consoleMethod(`[Yoru] ${message}`, extra || "");
    }
  } catch {
    // Silently fail - logging should never block the plugin
    const consoleMethod = level === "error" ? console.error : level === "warn" ? console.warn : console.log;
    consoleMethod(`[Yoru] ${message}`, extra || "");
  }
}

async function loadYoruConfig(): Promise<YoruConfig | null> {
  try {
    // @ts-ignore - Node/Bun runtime module; local standalone typecheck has no @types/node
    const fs = await import("fs/promises");
    // @ts-ignore - Node/Bun runtime module; local standalone typecheck has no @types/node
    const path = await import("path");
    // @ts-ignore - Node/Bun runtime module; local standalone typecheck has no @types/node
    const os = await import("os");

    const configPath = path.join(os.homedir(), ".config", "yoru", "config.json");
    const configContent = await fs.readFile(configPath, "utf-8");
    return JSON.parse(configContent);
  } catch {
    return null;
  }
}

export async function sendEventToYoru(config: YoruConfig, event: YoruEvent): Promise<boolean> {
  try {
    const url = `${config.server.replace(/\/$/, "")}/api/v1/sessions/events`;

    const response = await globalThis.fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${config.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ events: [event] }),
      signal: AbortSignal.timeout(2000), // 2 second timeout
    });

    return response.ok;
  } catch {
    return false;
  }
}

function mapOpenCodeEventToYoru(event: any, sessionId: string): YoruEvent | null {
  const timestamp = new Date().toISOString();

  switch (event.type) {
    case "session.created":
      return {
        session_id: sessionId,
        kind: "session_start",
        ts: timestamp,
        agent: "opencode",
        raw: { opencode_event: event },
      };

    case "session.idle":
    case "session.status":
      return {
        session_id: sessionId,
        kind: "session_end",
        ts: timestamp,
        agent: "opencode",
        raw: { opencode_event: event },
      };

    case "tool.execute.after":
      return {
        session_id: sessionId,
        kind: "tool_use",
        ts: timestamp,
        tool: event.properties?.tool || "unknown",
        content: event.properties?.output || "",
        agent: "opencode",
        raw: { opencode_event: event },
      };

    case "file.edited":
      return {
        session_id: sessionId,
        kind: "file_change",
        ts: timestamp,
        path: event.properties?.path,
        agent: "opencode",
        raw: { opencode_event: event },
      };

    default:
      // Ignore other events for now
      return null;
  }
}

export const YoruPlugin: Plugin = async ({ client, directory }: OpenCodePluginContext) => {
  const config = await loadYoruConfig();

  if (!config) {
    await logYoru(client, "warn", "No config found at ~/.config/yoru/config.json - plugin disabled");
    return {};
  }

  await logYoru(client, "info", `Plugin loaded, streaming events to ${config.server}`);

  return {
    event: async ({ event }: OpenCodeEventEnvelope) => {
      // Extract session ID from event properties
      const sessionId = event.properties?.sessionID || event.properties?.sessionId;

      if (!sessionId) {
        return; // Skip events without session ID
      }

      // Map OpenCode event to Yoru format
      const yoruEvent = mapOpenCodeEventToYoru(event, sessionId);

      if (!yoruEvent) {
        return; // Skip unmapped events
      }

      // Add working directory context if available
      if (directory) {
        yoruEvent.cwd = directory;
      }

      // Send to Yoru (non-blocking)
      sendEventToYoru(config, yoruEvent).catch((err) => {
        logYoru(client, "error", `Failed to send event: ${err}`, { error: String(err) });
      });
    },
  };
};
