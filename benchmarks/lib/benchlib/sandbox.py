"""Run untrusted graded code through benchmarks/scripts/bench-sandbox.sh (doc 05 D2).

Thin wrapper so runners don't hard-code the sandbox path. The sandbox gives the
graded command a read-only host, a single writable ``/tmp`` (a fresh tmpfs or
the ``--stage`` dir), no network, and optional wall/mem/cpu caps.
"""

import subprocess

from . import SANDBOX_SH


def run(
    cmd, *, stage=None, time=None, mem=None, cpu=None, **kw
) -> subprocess.CompletedProcess:
    """Run ``cmd`` (a list) inside the sandbox. Extra kwargs go to subprocess.run."""
    args = ["bash", str(SANDBOX_SH)]
    if stage is not None:
        args += ["--stage", str(stage)]
    if time is not None:
        args += ["--time", str(time)]
    if mem is not None:
        args += ["--mem", str(mem)]
    if cpu is not None:
        args += ["--cpu", str(cpu)]
    args += ["--", *[str(c) for c in cmd]]
    return subprocess.run(args, **kw)
