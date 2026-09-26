#!/usr/bin/env bun
/**
 * Local verification test for OpenCode plugin sendEventToYoru function.
 *
 * This test verifies that sendEventToYoru correctly posts { events: [event] }
 * and returns true on success using a mocked global fetch.
 */

import { sendEventToYoru } from "./opencode-plugin";

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

async function testSendEventToYoru() {
  console.log("Testing sendEventToYoru function...");

  // Mock global fetch to intercept the call
  let capturedRequest: { url: string; body: string; headers: HeadersInit } | null = null;

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url: string | Request, init?: RequestInit) => {
    capturedRequest = {
      url: url.toString(),
      body: init?.body as string,
      headers: init?.headers as HeadersInit,
    };

    // Return a mock Response
    return {
      ok: true,
      status: 202,
      json: async () => ({ accepted: 1, session_ids: ["test-session"], flagged_sessions: [] }),
    } as Response;
  };

  try {
    const config: YoruConfig = {
      server: "http://localhost:8002",
      token: "test-token",
    };

    const event: YoruEvent = {
      session_id: "test-session-123",
      kind: "tool_use",
      ts: new Date().toISOString(),
      tool: "Bash",
      content: "ls -la",
      agent: "opencode",
    };

    const result = await sendEventToYoru(config, event);

    console.log("sendEventToYoru returned:", result);

    if (capturedRequest) {
      console.log("Captured request URL:", capturedRequest.url);
      console.log("Captured request body:", capturedRequest.body);

      const parsedBody = JSON.parse(capturedRequest.body);
      console.log("Parsed body:", JSON.stringify(parsedBody, null, 2));

      // Verify the request structure
      if (parsedBody.events && Array.isArray(parsedBody.events) && parsedBody.events.length === 1) {
        console.log("Body has correct structure: { events: [event] }");
        console.log("Event in body:", JSON.stringify(parsedBody.events[0], null, 2));
      } else {
        console.error("Body structure incorrect");
        process.exit(1);
      }

      // Verify headers
      if (capturedRequest.headers && typeof capturedRequest.headers === 'object') {
        const headers = capturedRequest.headers as Record<string, string>;
        if (headers["Authorization"] === "Bearer test-token") {
          console.log("Authorization header correct");
        } else {
          console.error("Authorization header incorrect");
          process.exit(1);
        }
        if (headers["Content-Type"] === "application/json") {
          console.log("Content-Type header correct");
        } else {
          console.error("Content-Type header incorrect");
          process.exit(1);
        }
      }
    } else {
      console.error("No request captured");
      process.exit(1);
    }

    if (result === true) {
      console.log("sendEventToYoru test passed!");
      process.exit(0);
    } else {
      console.error("sendEventToYoru returned false");
      process.exit(1);
    }
  } catch (error) {
    console.error("Test failed with error:", error);
    process.exit(1);
  } finally {
    // Restore original fetch
    globalThis.fetch = originalFetch;
  }
}

testSendEventToYoru();