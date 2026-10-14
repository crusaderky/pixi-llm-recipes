#!/usr/bin/env python3
"""Every command-line parameter Strata accepts, and where this project sets them.

    pixi run strata-help
    pixi run strata-help -- --kv q4_0 --context 131072   # what a run would look like

The first half is ``strata.ini``: where it is, how it is written, and what it says right now.
The second half is Strata's own ``setup.py --help``, printed by setup.py itself, so the flag
list cannot drift from the version this project packages.
"""

from __future__ import annotations

import sys

from strata_common import (
    COMMAND_FLAGS,
    ENV_FLAGS,
    GGUF_DIR_FLAG,
    SAMPLING_FLAGS,
    START_FLAGS,
    VALUELESS_FLAGS,
    WRAPPER_FLAGS,
    app_dir,
    config_sampling,
    ini_path,
    load_setup,
    model_configs,
    prepare_flags,
    render,
    run_setup,
    sampling_values,
    strata_args,
    without,
)


def main() -> int:
    cli = [a for a in sys.argv[1:] if a != "--"]
    pairs = strata_args(cli)
    path = ini_path()
    setup = load_setup(app_dir())

    print(f"strata.ini: {path}")
    print(
        "  every key is an argument for Strata's setup.py: `key = value`, dashes or "
        "underscores,\n  the leading `--` optional. A key with no value is a flag on its own "
        "(\n  "
        + ", ".join(sorted(VALUELESS_FLAGS - COMMAND_FLAGS))
        + "); `off`/`no`/`false` drops one."
    )
    print(
        "  precedence: command line > " + ", ".join(sorted(ENV_FLAGS)) + " > strata.ini"
    )
    print(
        "  applied to every start: " + ", ".join(sorted(START_FLAGS)) + "\n"
        "  read only when the model is prepared (changing one re-prepares it): "
        + ", ".join(prepare_flags(setup))
    )
    print(
        "  refused here (one-shot commands, give them after `--`): "
        + ", ".join(sorted(COMMAND_FLAGS))
        + f", {GGUF_DIR_FLAG} (set by scripts/strata-run.py)\n"
        "  this wrapper's own: " + ", ".join(sorted(WRAPPER_FLAGS))
    )
    sampling = sampling_values(pairs)
    print(
        "  sampling defaults -- setup.py takes no sampling flag, because its start path runs "
        "the\n  engine with whatever its run config already says.  These go into that config's\n"
        "  `sampling` block (scripts/strata-run.py rewrites it before every start) and answer "
        "only\n  what a request leaves out: a client's own temperature still wins, and Strata's "
        "Chat\n  settings (POST /settings) sit on top of them: "
        + ", ".join(sorted(SAMPLING_FLAGS))
    )
    if sampling:
        print(
            "  strata.ini sets: "
            + ", ".join(f"{key}={value}" for key, value in sampling.items())
        )
    installed = model_configs(app_dir())
    if installed:
        block = config_sampling(installed[0])
        print(
            f"  {installed[0].name} says right now: "
            + (
                ", ".join(f"{key}={value}" for key, value in sorted(block.items()))
                if block
                else "no sampling block yet"
            )
        )
    print()
    print("this run would use:")
    print("  " + " ".join(render(without(pairs, SAMPLING_FLAGS))))
    print()
    print(f"every parameter {app_dir() / 'setup.py'} accepts:")
    return run_setup(setup, ["--help"])


if __name__ == "__main__":
    sys.exit(main())
