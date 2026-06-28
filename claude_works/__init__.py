"""claude_works: a Model Context Protocol server over a fit-first job-search loop.

The package wraps an existing, working autonomous job-application system (discovery
sweeps, a 4-gate resume verifier, a fill-and-park submission policy, and an
application ledger) behind a clean MCP tool surface. The core modules
(``models``, ``config``, ``discovery``, ``resume``, ``submission``, ``tracker``)
import with only the standard library plus the repo's own scripts, so they can be
used and tested without the MCP runtime. ``server`` adds the FastMCP wiring.
"""

from .models import Application, Job, Resume, Score, SearchAngle

__version__ = "0.1.0"

__all__ = [
    "Application",
    "Job",
    "Resume",
    "Score",
    "SearchAngle",
    "__version__",
]
