#!/bin/bash
# Point pi at the local Strata server, idempotently.
#
# pi keeps OpenAI-compatible endpoints in ~/.pi/agent/models.json (settings.json is for
# packages, tools and theme), and its openai-completions client reads Strata's
# reasoning_content, so the thinking block renders as thinking. Both launchers call this
# before starting pi: bwrap-pi.sh binds the host's models.json/settings.json into the
# sandbox, so a file edited here is what the sandboxed pi reads too.
#
# An existing `strata` provider is left untouched -- hand edits win -- but a provider still
# pointing at a port the active profile no longer serves is called out. Set STRATA_PROFILE
# to point pi at a profile other than the first one in strata.ini.

set -o errexit
set -o nounset

AGENT_DIR="${HOME}/.pi/agent"
MODELS="${AGENT_DIR}/models.json"
SETTINGS="${AGENT_DIR}/settings.json"

mkdir -p "${AGENT_DIR}"
if [ ! -f "${MODELS}" ]; then
    echo '{"providers": {}}' > "${MODELS}"
fi

# The active profile defines these. The port and context come from strata.ini (STRATA_PORT
# wins), the model name from that profile's run config once it is installed. Several profiles
# share this env, so "the newest run config" would be the wrong answer after a second install.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${STRATA_PORT:-$(python "${SCRIPT_DIR}/strata-run.py" --print port 2> /dev/null || true)}"
CONTEXT="$(python "${SCRIPT_DIR}/strata-run.py" --print context 2> /dev/null || true)"
MODEL="$(python "${SCRIPT_DIR}/strata-run.py" --print model-name 2> /dev/null || true)"
if [ -z "${MODEL}" ]; then
    MODEL="strata"
fi

if [ -z "${PORT}" ] || [ -z "${CONTEXT}" ]; then
    echo "strata: neither strata.ini nor an installed run config says where Strata serves; models.json left alone"
    exit 0
fi

node - "${MODELS}" "${SETTINGS}" "http://127.0.0.1:${PORT}/v1" "${MODEL}" "${CONTEXT}" <<'JS'
const fs = require("fs");
const [modelsPath, settingsPath, baseUrl, modelId, contextWindow] = process.argv.slice(2);

const models = JSON.parse(fs.readFileSync(modelsPath, "utf8"));
const settings = fs.existsSync(settingsPath) ? JSON.parse(fs.readFileSync(settingsPath, "utf8")) : {};
models.providers = models.providers ?? {};

const existing = models.providers.strata;
if (existing) {
  // hand-edited (or written when the port was different): only report a stale port
  const port = (existing.baseUrl ?? "").match(/:(\d+)/);
  const wanted = baseUrl.match(/:(\d+)/);
  if (port && wanted && port[1] !== wanted[1]) {
    console.log(`strata: models.json still points at :${port[1]}, the installed config serves :${wanted[1]}`);
  }
} else {
  models.providers.strata = {
    baseUrl,
    api: "openai-completions",
    // Strata authenticates nothing; the field is what makes the model selectable in pi
    apiKey: "strata",
    models: [
      {
        id: modelId,
        name: "Strata",
        reasoning: true,
        input: ["text", "image"],
        contextWindow: Number(contextWindow),
        maxTokens: 16384,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      },
    ],
  };
  fs.writeFileSync(modelsPath, JSON.stringify(models, null, 2) + "\n");
  console.log(`strata: added the "${modelId}" provider to models.json (${baseUrl}, ${contextWindow} tokens)`);
}

// a curated model cycle gets the new model too; without enabledModels every model is cycled
if (Array.isArray(settings.enabledModels)) {
  const pattern = `strata/${modelId}`;
  if (!settings.enabledModels.includes(pattern)) {
    settings.enabledModels.push(pattern);
    fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
    console.log(`strata: added ${pattern} to enabledModels`);
  }
}
JS
