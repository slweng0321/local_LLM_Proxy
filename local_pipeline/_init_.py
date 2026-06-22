"""local_pipeline package.

Keep package-level imports intentionally minimal to avoid import-time side
effects and circular dependencies between API, pipeline, and infrastructure
modules.
"""

from .schemas import RetrievedFile, TaskState

__all__ = [
    "RetrievedFile",
    "TaskState",
]

__version__ = "0.1.0"