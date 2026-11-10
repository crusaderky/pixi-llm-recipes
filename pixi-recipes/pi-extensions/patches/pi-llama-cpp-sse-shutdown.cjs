#!/usr/bin/env node
// Apply the SSE-shutdown fix to an installed pi-llama-cpp.
//
// Why: pi-llama-cpp opens a Server-Sent Events stream to llama-server to
// follow a model load, and the stream's reconnect loop runs until disconnect()
// is called. Nothing calls it: pi closes its process only once no handles are
// left, so `pi -p` prints its answer and then never exits — which is what
// wedged every `pi -p` step in .github/workflows/pi.yml (a llama-server
// --models-preset router starts every model unloaded, so the load path, and
// with it the stream, is always taken). Reproduced against pi-llama-cpp
// 0.21.0 (npm latest), pi 1.5.1/1.5.2 and llama-server router mode; the
// process holds one ESTABLISHED socket to the server and never leaves it.
//
// The extension has a `session_shutdown`-shaped hole: pi's extension docs
// ("Long-lived resources and shutdown") require exactly that handler, and
// `grep -rn session_shutdown` over the package is empty. Disconnecting only
// the current servers is not enough either — ServerManager.update() re-creates
// every Server on a re-scan and `unregisterStaleProviders` skips the ones whose
// provider id survived, so the replaced instance keeps its SSE client
// reconnecting. This therefore tracks every client that subscribes and closes
// all of them.
//
// Upstream: https://github.com/gsanhueza/pi-llama-cpp/issues/40 — drop this
// file once a release that fixes it is pinned in recipe.yaml.
//
// Usage: node pi-llama-cpp-sse-shutdown.cjs <path to node_modules/pi-llama-cpp>
//
// Anchored and idempotent: an anchor that no longer matches is a hard error, so
// a pi-llama-cpp bump fails the package build rather than silently shipping an
// unpatched plugin. See update-pi-extensions.

const fs = require("fs");
const path = require("path");

const MARKER = "liveSSEClients";

const EXT_DIR = process.argv[2];
if (!EXT_DIR) {
  console.error(
    "usage: node pi-llama-cpp-sse-shutdown.cjs <extension dir> " +
      "(e.g. $PREFIX/home/.pi/agent/npm/node_modules/pi-llama-cpp)",
  );
  process.exit(2);
}

const indexTs = path.join(EXT_DIR, "src", "index.ts");
let version = "unknown";
try {
  version = JSON.parse(
    fs.readFileSync(path.join(EXT_DIR, "package.json"), "utf8"),
  ).version;
} catch {
  // keep "unknown" — the message below still names the file that failed
}

function fail(anchor) {
  console.error(
    `pi-llama-cpp-sse-shutdown: anchor not found in ${indexTs} ` +
      `(pi-llama-cpp ${version}):\n---\n${anchor}\n---\n` +
      "The patch no longer applies: re-derive it against this version, or drop " +
      "patches/pi-llama-cpp-sse-shutdown.cjs and the build.sh/build.bat call " +
      "if a release that closes the SSE stream on session_shutdown is pinned " +
      "(see https://github.com/gsanhueza/pi-llama-cpp/issues/40).",
  );
  process.exit(1);
}

function replaceOnce(source, anchor, replacement) {
  if (!source.includes(anchor)) fail(anchor);
  return source.replace(anchor, replacement);
}

let source = fs.readFileSync(indexTs, "utf8");

if (source.includes(MARKER)) {
  console.log(
    `pi-llama-cpp-sse-shutdown: already applied (pi-llama-cpp ${version})`,
  );
  process.exit(0);
}

// 1. the SSEClient class, so the prototype can be wrapped
source = replaceOnce(
  source,
  'import { ServerFactory } from "./servers/serverFactory";\n',
  'import { ServerFactory } from "./servers/serverFactory";\nimport { SSEClient } from "./sse/client";\n');

// 2. the tracking itself, at module scope
const moduleScope = `/**
 * Every SSE client that has subscribed since this module was loaded.
 *
 * A client reconnects to llama-server until disconnect() is called, and an
 * open stream keeps Node's event loop alive, so a client left connected stops
 * \`pi -p\` from ever exiting. The clients are tracked rather than read off the
 * live servers because ServerManager.update() re-creates every Server on a
 * re-scan and leaves the replaced instances' clients running.
 */
const ${MARKER} = new Set<SSEClient>();
const trackedSubscribe = SSEClient.prototype.subscribe;
SSEClient.prototype.subscribe = function (
  this: SSEClient,
  ...args: Parameters<typeof trackedSubscribe>
) {
  ${MARKER}.add(this);
  return trackedSubscribe.apply(this, args);
};

export default async function (pi: ExtensionAPI) {`;
source = replaceOnce(
  source,
  "export default async function (pi: ExtensionAPI) {",
  moduleScope,
);

// 3. the handler pi's extension docs prescribe for long-lived resources
const handler = `  // Clear tracked completions when the agent finishes
  pi.on("message_end", () => eventManager.onMessageEnd());

  // Close every llama-server SSE stream: they reconnect forever, and an open
  // one is a handle pi cannot finish without. session_shutdown is where pi's
  // extension docs put long-lived resources.
  pi.on("session_shutdown", () => {
    for (const client of ${MARKER}) client.disconnect();
  });
}`;
source = replaceOnce(
  source,
  '  // Clear tracked completions when the agent finishes\n  pi.on("message_end", () => eventManager.onMessageEnd());\n}',
  handler,
);

fs.writeFileSync(indexTs, source);
console.log(`pi-llama-cpp-sse-shutdown: applied (pi-llama-cpp ${version})`);
