"""MAKO's own config validator: `mako-cli validate -c <file>`.

Used as a second safety net before Pescao replaces conf.toml: MAKO judges its own format
(it rejects, e.g., out-of-range values and unknown methods), so a newer MAKO's rules
are honoured even when Pescao doesn't know them. Optional: without mako-cli Pescao
relies on its own checks.

Pure stdlib, no Invasor imports.
"""
import shutil
import subprocess
from pathlib import Path

TIMEOUT = 5
CLI = "mako-cli"


class Rejected(Exception):
    """MAKO says the file isn't valid; the message is MAKO's own."""


def find_cli(library=None):
    """mako-cli next to the layer library in use (<prefix>/bin), else on PATH, else None."""
    if library:
        candidate = Path(library).parent.parent / "bin" / CLI
        if candidate.is_file():
            return str(candidate)
    return shutil.which(CLI)


def validate(path, cli):
    """None if MAKO accepts the file. Raises Rejected with MAKO's message if not,
    or OSError/TimeoutError if the validator can't be run (treat as unavailable)."""
    try:
        run = subprocess.run([cli, "validate", "-c", str(path)], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"{CLI} didn't answer in {TIMEOUT}s") from None
    if run.returncode == 0:
        return None
    lines = [ln.strip() for ln in (run.stdout + "\n" + run.stderr).splitlines() if ln.strip()]
    # "Validation failed: <reason>", sometimes followed by details ("- Error while parsing…").
    reasons = [ln.removeprefix("Validation failed:").strip() for ln in lines if ln != "Validation success"]
    reasons = [r for r in reasons if r]
    raise Rejected(" ".join(reasons) if reasons else f"{CLI} exited with {run.returncode}")
