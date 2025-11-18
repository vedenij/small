"""
Delegation module for PoC computation offloading.

This module enables small nodes to delegate PoC computation to big nodes
while maintaining control over signing and blockchain submission.
"""

from pow.service.delegation.client import DelegationClient
from pow.service.delegation.server import DelegationManager, DelegationSession
from pow.service.delegation.types import (
    DelegationStartRequest,
    DelegationBatchResponse,
    DelegationStatus,
)

__all__ = [
    "DelegationClient",
    "DelegationManager",
    "DelegationSession",
    "DelegationStartRequest",
    "DelegationBatchResponse",
    "DelegationStatus",
]
