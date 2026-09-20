"""jevcode — a coding agent driven by a model that cannot write a single character.

Jev answers typed questions with calibrated probabilities and nothing else. That
sounds like a limitation, and it is, but it is also a licence to make decisions
the way no ordinary agent can afford to: hundreds of them, in parallel, before
every move, for a fraction of a cent. The code writing is handed to a small fast
model that is told exactly what to write and never asked what to do.
"""

__version__ = "0.1.0"

from .engine import Agent, Outcome           # noqa: F401
from .systemone import SystemOne, Usage      # noqa: F401
from .writer import Writer, WriterUsage      # noqa: F401
