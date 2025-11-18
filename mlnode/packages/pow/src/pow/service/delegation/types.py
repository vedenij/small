"""
Type definitions for PoC delegation.
"""

from typing import List, Optional
from enum import Enum
from pydantic import BaseModel

from pow.models.utils import Params
from pow.data import ProofBatch


class DelegationStatus(str, Enum):
    """Status of a delegation session."""
    INITIALIZING = "initializing"
    GENERATING = "generating"
    STOPPED = "stopped"
    EXPIRED = "expired"
    ERROR = "error"


class DelegationStartRequest(BaseModel):
    """
    Request to start a delegation session on big node.

    Sent from small node to big node to initiate PoC computation.
    """
    node_id: int
    node_count: int
    block_hash: str
    block_height: int
    public_key: str
    batch_size: int
    r_target: float
    fraud_threshold: float
    params: Params
    auth_token: str

    # Optional callback URL if big node should push batches
    callback_url: Optional[str] = None


class DelegationBatchResponse(BaseModel):
    """
    Response containing batches from delegation session.

    Returned when small node polls for batches.
    """
    session_id: str
    batches: List[ProofBatch]
    session_active: bool
    status: DelegationStatus
    total_batches_generated: int

    class Config:
        # Allow ProofBatch to be serialized
        arbitrary_types_allowed = True


class DelegationStartResponse(BaseModel):
    """Response when delegation session is started."""
    session_id: str
    status: DelegationStatus
    message: str
    gpu_count: int  # Number of GPUs on big node


class DelegationStopRequest(BaseModel):
    """Request to stop a delegation session."""
    session_id: str
    auth_token: str


class DelegationStopResponse(BaseModel):
    """Response when delegation session is stopped."""
    session_id: str
    status: DelegationStatus
    message: str
    total_batches_generated: int
