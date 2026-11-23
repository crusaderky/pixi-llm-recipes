#!/usr/bin/env python3
"""Every command-line parameter Strata accepts, and where this project sets them.

    pixi run strata-help                 # the first profile in strata.ini
    pixi run strata-help QFN             # one named profile or alias
    pixi run strata-help QFN -- --kv q4_0 --context 131072   # what a run would look like

The first half is ``strata.ini``: where it is, which profile is active, how it is written,
and what it says right now.
The second half is Strata's own ``setup.py --help``, printed by setup.py itself, so the flag
list cannot drift from the version this project packages.
"""

from __future__ import annotations

import os
import sys

from strata_common import (
    COMMAND_FLAGS,
    ENV_FLAGS,
    GGUF_DIR_FLAG,
    PROFILE_ENV,
    START_FLAGS,
    VALUELESS_FLAGS,
    WRAPPER_FLAGS,
    app_dir,
    ini_path,
    load_setup,
    prepare_flags,
    profiles,
    render,
    run_setup,
    select_profile,
    setup_file,
    strata_args,
)


def main() -> int:
    cli = [a for a in sys.argv[1:] if a != "--"]
    profile = os.environ.get(PROFILE_ENV)
    if cli and not cli[0].startswith("--"):
        profile = cli.pop(0)
    if profile:
        os.environ[PROFILE_ENV] = profile
    pairs = strata_args(cli)
    path = ini_path()
    setup = load_setup(app_dir())

    print(f"strata.ini: {path}")
    print(
        "  profiles: "
        + ", ".join(
            name + (f" ({', '.join(aliases)})" if aliases else "")
            for name, aliases in profiles(path)
        )
    )
    print(f"  active profile: {select_profile(profile, path)}")
    print(
        "  every key is an argument for Strata's setup.py: `key = value`, dashes or "
        "underscores,\n  the leading `--` optional. A key with no value is a flag on its own "
        "(\n  "
        + ", ".join(sorted(VALUELESS_FLAGS - COMMAND_FLAGS))
        + "); `off`/`no`/`false` drops one."
    )
    print(
        "  profile: task argument > STRATA_PROFILE > the first section of strata.ini\n"
        "  key precedence: command line > "
        + ", ".join(sorted(ENV_FLAGS))
        + " > the profile"
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
    print()
    print("this run would use:")
    print("  " + " ".join(render(pairs)))
    print()
    print(f"every parameter {setup_file(app_dir())} accepts:")
    return run_setup(setup, ["--help"])


if __name__ == "__main__":
    sys.exit(main())
