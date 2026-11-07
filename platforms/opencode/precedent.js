// precedent for OpenCode: automatic enforcement, same checks as the Claude Code,
// Codex CLI and Gemini CLI hooks (it runs those very scripts).
//
// One file, both plugin APIs: OpenCode V2 loads the default export {id, setup};
// classic OpenCode (@opencode-ai/plugin) calls `server` / `PrecedentPlugin`.
//
// - tool.execute.before  -> integrity_guard.py: blocks test weakening and destructive shortcuts
// - tool.execute.after   -> failure_tracker.py: breaks same-error loops (note appended to the tool output)
// - chat.message         -> prompt_trigger.py:  "you did it before" gets real precedent or an honest "none found"
// - system prompt        -> the precedent rules, plus any precedent found for this turn
// - session.idle         -> stop_guard.py:      a give-up or an unchecked "done" is sent back once
//
// Local only. Fails open: any error here is logged and ignored, never blocks you.
// PRECEDENT_OFF=1 disables everything. PRECEDENT_OPENCODE_LOG=<file> writes a debug log.

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function loadSettings() {
  // Written by install.py next to this file: {"python": ..., "hooks": ..., "rules": ...}
  try {
    return JSON.parse(fs.readFileSync(path.join(HERE, "precedent.config.json"), "utf8"));
  } catch {
    return {};
  }
}

const SETTINGS = loadSettings();
const PYTHON = process.env.PRECEDENT_PYTHON || SETTINGS.python || "python";
const HOOKS = process.env.PRECEDENT_HOOKS_DIR || SETTINGS.hooks || path.resolve(HERE, "..", "..", "hooks");
const RULES_FILE = SETTINGS.rules || path.resolve(HERE, "..", "rules", "precedent-rules.md");

function disabled() {
  return /^(1|true|yes|on)$/i.test(process.env.PRECEDENT_OFF || "");
}

function log(...parts) {
  const file = process.env.PRECEDENT_OPENCODE_LOG;
  if (!file) return;
  const line = parts.map((p) => (typeof p === "string" ? p : JSON.stringify(p))).join(" ");
  try {
    fs.appendFileSync(file, `${new Date().toISOString()} ${line}\n`);
  } catch {
    // logging must never break the agent
  }
}

function readRules() {
  try {
    return fs.readFileSync(RULES_FILE, "utf8");
  } catch {
    return "";
  }
}

function runHook(script, payload, timeoutMs = 20000) {
  // Resolves to the hook's JSON reply, or null on any problem (fail open).
  return new Promise((resolve) => {
    if (disabled()) return resolve(null);
    let settled = false;
    const finish = (value) => {
      if (!settled) {
        settled = true;
        resolve(value);
      }
    };
    try {
      const child = spawn(PYTHON, [path.join(HOOKS, script), "--agent=opencode"], {
        env: { ...process.env, PYTHONIOENCODING: "utf-8" },
        windowsHide: true,
      });
      let stdout = "";
      child.stdout.on("data", (chunk) => (stdout += chunk));
      child.on("error", (error) => {
        log("hook spawn error", script, String(error));
        finish(null);
      });
      child.on("close", () => {
        const text = stdout.trim();
        log("hook", script, payload.hook_event_name, text.slice(0, 400));
        try {
          finish(text ? JSON.parse(text) : null);
        } catch {
          finish(null);
        }
      });
      const timer = setTimeout(() => {
        log("hook timeout", script);
        child.kill();
        finish(null);
      }, timeoutMs);
      child.on("close", () => clearTimeout(timer));
      child.stdin.end(JSON.stringify(payload));
    } catch (error) {
      log("hook error", script, String(error));
      finish(null);
    }
  });
}

function lastTurn(messages) {
  // Messages from the last real user message onward.
  let start = 0;
  messages.forEach((message, index) => {
    const isUser = message?.info?.role === "user";
    const hasRealText = (message?.parts || []).some((p) => p?.type === "text" && !p.synthetic && String(p.text || "").trim());
    if (isUser && hasRealText) start = index;
  });
  return messages.slice(start);
}

function makeState() {
  return {
    turnContext: new Map(), // sessionID -> precedent text for this turn's system prompt
    sentBack: new Set(), // sessions we already sent back once (never twice in a row)
    ownPrompts: new Set(), // texts we injected ourselves
    checked: new Set(), // V2: sessions whose current turn was already checked
    lastReply: new Map(), // last assistant text per session, so pushback can tell a reply exists
  };
}

async function onPrompt(state, sessionID, text, directory) {
  text = String(text || "").trim();
  if (!text || disabled()) return;
  if (state.ownPrompts.has(text)) {
    state.ownPrompts.delete(text);
    return;
  }
  state.sentBack.delete(sessionID);
  state.checked.delete(sessionID);
  state.turnContext.delete(sessionID);
  const reply = await runHook("prompt_trigger.py", {
    hook_event_name: "UserPromptSubmit",
    session_id: sessionID,
    cwd: directory,
    prompt: text,
    last_assistant_message: state.lastReply.get(sessionID) || "",
  });
  if (reply?.additionalContext) state.turnContext.set(sessionID, reply.additionalContext);
}

function systemTexts(state, sessionID) {
  if (disabled()) return [];
  const texts = [];
  const rules = readRules();
  if (rules) texts.push(rules);
  const extra = sessionID && state.turnContext.get(sessionID);
  if (extra) texts.push(extra);
  return texts;
}

async function guardTool(sessionID, tool, args, directory) {
  // Throws to block the call; the agent sees the reason as the tool's error.
  let reply = null;
  log("tool", tool, Object.keys(args || {}));
  try {
    reply = await runHook("integrity_guard.py", {
      hook_event_name: "PreToolUse",
      session_id: sessionID,
      cwd: directory,
      tool_name: tool,
      tool_input: args || {},
    });
  } catch (error) {
    log("guardTool error", String(error));
  }
  if (reply && (reply.permissionDecision === "deny" || reply.permissionDecision === "ask")) {
    log("blocked tool", tool);
    throw new Error(reply.permissionDecisionReason || "[precedent] blocked");
  }
}

async function trackResult(sessionID, tool, args, outputText, exitCode, directory) {
  // Returns a note to append to the tool output, or "".
  try {
    const reply = await runHook("failure_tracker.py", {
      hook_event_name: "PostToolUse",
      session_id: sessionID,
      cwd: directory,
      tool_name: tool,
      tool_input: args || {},
      tool_response: { output: String(outputText ?? ""), exit_code: exitCode },
    });
    return reply?.additionalContext || "";
  } catch (error) {
    log("trackResult error", String(error));
    return "";
  }
}

async function checkStop(state, sessionID, messages, directory, sendBack) {
  if (disabled() || !sessionID) return;
  if (state.sentBack.has(sessionID)) {
    state.sentBack.delete(sessionID);
    return;
  }
  const turn = lastTurn(messages);
  const replyText = turn
    .filter((message) => message?.info?.role === "assistant")
    .flatMap((message) => (message.parts || []).filter((part) => part?.type === "text").map((part) => part.text || ""))
    .join("\n")
    .trim();
  if (replyText) state.lastReply.set(sessionID, replyText.slice(-4000));
  const reply = await runHook("stop_guard.py", {
    hook_event_name: "Stop",
    session_id: sessionID,
    cwd: directory,
    stop_hook_active: false,
    opencode_messages: turn,
  });
  if (reply?.decision === "block" && reply.reason) {
    state.sentBack.add(sessionID);
    state.ownPrompts.add(reply.reason.trim());
    log("sending back", sessionID);
    await sendBack(reply.reason);
  }
}

// ---------------------------------------------------------------- classic API

const PrecedentPlugin = async ({ client, directory }) => {
  const state = makeState();
  log("loaded classic", { directory, python: PYTHON, hooks: HOOKS, off: disabled() });

  return {
    "chat.message": async (input, output) => {
      try {
        const text = (output?.parts || [])
          .filter((p) => p?.type === "text" && !p.synthetic)
          .map((p) => p.text)
          .join("\n");
        await onPrompt(state, input.sessionID, text, directory);
      } catch (error) {
        log("chat.message error", String(error));
      }
    },

    "experimental.chat.system.transform": async (input, output) => {
      try {
        output.system.push(...systemTexts(state, input?.sessionID));
      } catch (error) {
        log("system.transform error", String(error));
      }
    },

    "tool.execute.before": async (input, output) => {
      await guardTool(input.sessionID, input.tool, output?.args, directory);
    },

    "tool.execute.after": async (input, output) => {
      const note = await trackResult(
        input.sessionID,
        input.tool,
        input.args,
        output?.output,
        output?.metadata?.exit,
        directory,
      );
      if (note) output.output = `${output.output ?? ""}\n\n${note}`;
    },

    event: async ({ event }) => {
      try {
        if (event?.type !== "session.idle") return;
        const sessionID = event.properties?.sessionID;
        if (!sessionID || disabled()) return;
        const result = await client.session.messages({ path: { id: sessionID } });
        await checkStop(state, sessionID, result?.data ?? [], directory, (text) =>
          client.session.promptAsync({ path: { id: sessionID }, body: { parts: [{ type: "text", text }] } }),
        );
      } catch (error) {
        log("session.idle error", String(error));
      }
    },
  };
};

// --------------------------------------------------------------------- V2 API

function contentText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return content == null ? "" : JSON.stringify(content);
  return content
    .filter((part) => part && part.type === "text")
    .map((part) => part.text ?? "")
    .join("\n");
}

function entriesToMessages(entries) {
  // V2 session.context() entries -> the classic [{info: {role}, parts}] shape the hooks read.
  const messages = [];
  for (const entry of entries || []) {
    if (!entry || typeof entry !== "object") continue;
    if (entry.type === "assistant") {
      const parts = (entry.content || []).map((part) => {
        if (part?.type !== "tool") return { type: part?.type, text: part?.text };
        const state = part.state || {};
        return {
          type: "tool",
          callID: part.id,
          tool: part.name,
          state: {
            status: state.status,
            input: state.input,
            metadata: state.metadata,
            output: contentText(state.content ?? state.output),
          },
        };
      });
      messages.push({ info: { role: "assistant" }, parts });
    } else if (entry.type === "user") {
      messages.push({ info: { role: "user" }, parts: [{ type: "text", text: entry.text ?? "" }] });
    } else if (entry.type === "synthetic") {
      messages.push({ info: { role: "user" }, parts: [{ type: "text", text: entry.text ?? "", synthetic: true }] });
    }
  }
  return messages;
}

async function setup(ctx) {
  const directory = ctx.location?.directory || process.cwd();
  const state = makeState();
  const registrations = [];
  const register = async (label, fn) => {
    try {
      registrations.push(await fn());
    } catch (error) {
      log("V2 hook unavailable", label, String(error));
    }
  };
  log("loaded v2", { directory, python: PYTHON, hooks: HOOKS, off: disabled(), app: ctx.app });

  await register("session.prompt", () =>
    ctx.session.hook("prompt", async (event) => {
      try {
        await onPrompt(state, event.sessionID, event.prompt?.text, directory);
      } catch (error) {
        log("prompt error", String(error));
      }
    }),
  );

  await register("session.context", () =>
    ctx.session.hook("context", async (event) => {
      try {
        for (const text of systemTexts(state, event.sessionID)) event.system.push({ type: "text", text });
      } catch (error) {
        log("context error", String(error));
      }
    }),
  );

  await register("tool.execute.before", () =>
    ctx.tool.hook("execute.before", async (event) => {
      await guardTool(event.sessionID, event.tool, event.input, directory);
    }),
  );

  await register("tool.execute.after", () =>
    ctx.tool.hook("execute.after", async (event) => {
      try {
        const errored = event.status === "error";
        const result = event.result;
        const text = errored
          ? String(event.error?.message ?? event.error ?? "")
          : contentText(result?.content ?? result?.output);
        const exit = result?.metadata?.exit ?? (errored ? 1 : undefined);
        const note = await trackResult(event.sessionID, event.tool, event.input, text, exit, directory);
        if (!note || !result) return;
        if (Array.isArray(result.content)) result.content = [...result.content, { type: "text", text: `\n\n${note}` }];
        else result.content = `${text}\n\n${note}`;
      } catch (error) {
        log("execute.after error", String(error));
      }
    }),
  );

  const controller = new AbortController();
  const running = (async () => {
    try {
      for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
        // V2 ends a turn with session.execution.succeeded; older builds also emit session.idle.
        if (event?.type !== "session.execution.succeeded" && event?.type !== "session.idle") continue;
        const sessionID = event.data?.sessionID;
        if (!sessionID || disabled() || state.checked.has(sessionID)) continue;
        state.checked.add(sessionID);
        log("turn ended", sessionID, event.type);
        try {
          const entries = await ctx.session.context({ sessionID });
          await checkStop(state, sessionID, entriesToMessages(entries), directory, (text) =>
            ctx.session.prompt({ sessionID, text }),
          );
        } catch (error) {
          log("session.idle error", String(error));
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) log("event stream ended", String(error));
    }
  })();

  return async () => {
    controller.abort();
    await running.catch(() => {});
    for (const registration of registrations) {
      try {
        await registration?.dispose?.();
      } catch {
        // ignore
      }
    }
  };
}

// Only a default export: classic loaders call every named export as a plugin.
export default { id: "precedent", setup, server: PrecedentPlugin };
