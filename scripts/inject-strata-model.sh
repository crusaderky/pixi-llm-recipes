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
# pointing at a port the installed config no longer serves is called out.

set -o errexit
set -o nounset

AGENT_DIR="${HOME}/.pi/agent"
MODELS="${AGENT_DIR}/models.json"
SETTINGS="${AGENT_DIR}/settings.json"

mkdir -p "${AGENT_DIR}"
if [ ! -f "${MODELS}" ]; then
    echo '{"providers": {}}' > "${MODELS}"
fi

# The installed app tree: this env, a sibling env of it (the pixi layout of an agents env),
# $STRATA_ROOT, or this script's own repo. Its newest run config carries the port, the model
# name and the context size the server was prepared with.
APP=""
for candidate in "${STRATA_ROOT:-}" "${CONDA_PREFIX:-}/opt/strata" "${CONDA_PREFIX:-}/../strata/opt/strata" "$(dirname "$0")/../.pixi/envs/strata/opt/strata"; do
    if [ -n "${candidate}" ] && [ -f "${candidate}/setup.py" ]; then
        APP="${candidate}"
        break
    fi
done

# Defaults: the pixi tasks' port, a model name Strata accepts for anything (it ignores the
# field), and the context size the default setup settles on for a 24 GB card.
PORT="${STRATA_PORT:-8082}"
MODEL="strata"
CONTEXT=131072

CONFIG=""
if [ -n "${APP}" ]; then
    # the newest run config: a machine may have prepared several models, and each has its own
    for candidate in "${APP}"/strata-*.json; do
        [ -f "${candidate}" ] || continue
        if [ -z "${CONFIG}" ] || [ "${candidate}" -nt "${CONFIG}" ]; then
            CONFIG="${candidate}"
        fi
    done
fi
if [ -n "${CONFIG}" ] && [ -f "${CONFIG}" ]; then
    # one line: port model_name max-context
    CONFIG_KEYS="$(node -e '
const fs = require("fs");
const cfg = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const args = Array.isArray(cfg.args) ? cfg.args : [];
const flag = (name) => (args.indexOf(name) >= 0 ? Number(args[args.indexOf(name) + 1]) || 0 : 0);
process.stdout.write([cfg.port || 8082, cfg.model_name || "strata", flag("--max-context") || 131072].join(" "));
' "${CONFIG}")"
    read -r cfg_port cfg_model cfg_context <<< "${CONFIG_KEYS}"
    if [ -z "${STRATA_PORT:-}" ]; then
        PORT="${cfg_port}"
    fi
    MODEL="${cfg_model}"
    CONTEXT="${cfg_context}"
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
