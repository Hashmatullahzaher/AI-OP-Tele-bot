"""Production foundation for OS AI Core.

The browser demo under :mod:`app` remains intentionally separate.  This
package contains the reusable contracts, policy boundaries, and persistence
primitives that production milestones build on.
"""

from .config import RuntimeConfig
from .contracts import (
    CapabilityManifest,
    CapabilityNotAllowed,
    CapabilityRegistry,
    ContractError,
    ExecutionContext,
    PolicyDenied,
    SchemaValidationError,
    SourceProvenance,
    ToolExecutor,
    ToolResult,
)
from .storage import TenantSecurityStore

__all__ = [
    "CapabilityManifest",
    "CapabilityNotAllowed",
    "CapabilityRegistry",
    "ContractError",
    "ExecutionContext",
    "PolicyDenied",
    "RuntimeConfig",
    "SchemaValidationError",
    "SourceProvenance",
    "TenantSecurityStore",
    "ToolExecutor",
    "ToolResult",
]
