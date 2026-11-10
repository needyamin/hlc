"""Language-runtime metric adapters.

The common contract is ``RuntimeAdapter``. Every adapter must degrade to
``available() == False`` instead of failing a benchmark. Results are normalised
into the SAME field names (``metrics.runtime``), so a Python / Node / JVM / Go
comparison is apples-to-apples.
"""

from .base import (  # noqa: F401
    RuntimeAdapter, RemoteRuntimeAdapter, RUNTIME_FIELDS, normalize_runtime,
)
from .python_gc import LocalPythonAdapter, PythonRuntimeAdapter  # noqa: F401
from .node_v8 import NodeV8Adapter  # noqa: F401
from .jvm import JvmAdapter  # noqa: F401
from .go import GoAdapter  # noqa: F401
from .phpfpm import PhpFpmAdapter  # noqa: F401
from .dotnet import DotNetAdapter  # noqa: F401

REGISTRY = {
    "python_gc": PythonRuntimeAdapter,
    "local_python": LocalPythonAdapter,
    "node_v8": NodeV8Adapter,
    "jvm": JvmAdapter,
    "go": GoAdapter,
    "phpfpm": PhpFpmAdapter,
    "dotnet": DotNetAdapter,
}


def get(name, **kwargs):
    """Factory returning an adapter instance, or ``None`` for unknown names."""
    cls = REGISTRY.get(str(name or "").strip())
    return cls(**kwargs) if cls else None


__all__ = [
    "RuntimeAdapter", "RemoteRuntimeAdapter", "RUNTIME_FIELDS",
    "normalize_runtime", "LocalPythonAdapter", "PythonRuntimeAdapter",
    "NodeV8Adapter", "JvmAdapter", "GoAdapter", "PhpFpmAdapter",
    "DotNetAdapter", "REGISTRY", "get",
]
