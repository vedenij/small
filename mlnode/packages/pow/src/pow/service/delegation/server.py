"""
Delegation server components for big node.

Manages delegation sessions where small nodes offload PoC computation.
"""

import time
import threading
import uuid
from typing import Dict, List, Optional
from dataclasses import dataclass, field

import torch.multiprocessing as mp

from pow.compute.controller import ParallelController
from pow.data import ProofBatch
from pow.service.delegation.types import (
    DelegationStartRequest,
    DelegationStatus,
)
from common.logger import create_logger

logger = create_logger(__name__)

# Session timeout in seconds (5 minutes + 30 seconds buffer)
SESSION_TIMEOUT = 330


@dataclass
class DelegationSession:
    """
    Manages a single delegation session on big node.

    Attributes:
        session_id: Unique identifier for this session
        small_node_params: Parameters from small node
        created_at: Session creation timestamp
        last_poll_at: Last time small node polled for batches
        controller: ParallelController for batch generation
        batch_buffer: Thread-safe buffer for generated batches
        lock: Thread lock for batch_buffer access
        total_generated: Total number of batches generated
        status: Current session status
    """
    session_id: str
    small_node_params: DelegationStartRequest
    created_at: float = field(default_factory=time.time)
    last_poll_at: float = field(default_factory=time.time)
    controller: Optional[ParallelController] = None
    batch_buffer: List[ProofBatch] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    total_generated: int = 0
    status: DelegationStatus = DelegationStatus.INITIALIZING

    def add_batch(self, batch: ProofBatch) -> None:
        """
        Add a batch to buffer (thread-safe).

        Args:
            batch: ProofBatch to add to buffer
        """
        with self.lock:
            self.batch_buffer.append(batch)
            self.total_generated += 1
            logger.debug(
                f"Session {self.session_id}: Added batch, "
                f"buffer size: {len(self.batch_buffer)}, "
                f"total generated: {self.total_generated}"
            )

    def get_batches(self) -> List[ProofBatch]:
        """
        Return and clear batch buffer (thread-safe).

        Returns:
            List of batches from buffer
        """
        with self.lock:
            batches = self.batch_buffer.copy()
            self.batch_buffer.clear()
            logger.info(
                f"Session {self.session_id}: Retrieved {len(batches)} batches, "
                f"total generated: {self.total_generated}"
            )
            self.last_poll_at = time.time()
            return batches

    def is_expired(self) -> bool:
        """
        Check if session has expired.

        Session expires if:
        - More than SESSION_TIMEOUT seconds elapsed since creation
        - Controller is not running

        Returns:
            True if session is expired
        """
        if time.time() - self.created_at > SESSION_TIMEOUT:
            logger.info(f"Session {self.session_id}: Expired by timeout")
            return True

        if self.controller and not self.controller.is_running():
            logger.info(f"Session {self.session_id}: Controller not running")
            return True

        return False

    def start_generation(self) -> None:
        """
        Start batch generation using ParallelController.

        Creates controller with auto-detected GPUs and starts generation.
        """
        try:
            logger.info(
                f"Session {self.session_id}: Creating ParallelController "
                f"for node_id={self.small_node_params.node_id}, "
                f"node_count={self.small_node_params.node_count}"
            )

            # Create controller with auto-detected GPUs
            ctx = mp.get_context("spawn")
            self.controller = ParallelController(
                params=self.small_node_params.params,
                block_hash=self.small_node_params.block_hash,
                block_height=self.small_node_params.block_height,
                public_key=self.small_node_params.public_key,
                node_id=self.small_node_params.node_id,
                node_count=self.small_node_params.node_count,
                batch_size=self.small_node_params.batch_size,
                r_target=self.small_node_params.r_target,
                devices=None,  # Auto-detect GPUs
            )

            # Start controller
            self.controller.start()
            logger.info(
                f"Session {self.session_id}: Controller started with "
                f"{len(self.controller.controllers)} GPU groups"
            )

            # Start generation phase
            self.controller.start_generate()
            self.status = DelegationStatus.GENERATING
            logger.info(f"Session {self.session_id}: Generation started")

            # Start batch collection thread
            self._start_batch_collector()

        except Exception as e:
            logger.error(f"Session {self.session_id}: Failed to start generation: {e}")
            self.status = DelegationStatus.ERROR
            raise

    def _start_batch_collector(self) -> None:
        """
        Start background thread to collect batches from controller.

        Thread continuously polls controller's generated_batch_queue
        and adds batches to session buffer.
        """
        def collect_batches():
            logger.info(f"Session {self.session_id}: Batch collector thread started")
            while self.status == DelegationStatus.GENERATING:
                try:
                    # Get generated batches from controller
                    batches = self.controller.get_generated()
                    if batches:
                        # Merge and add to buffer
                        merged_batch = ProofBatch.merge(batches)
                        if len(merged_batch.nonces) > 0:
                            self.add_batch(merged_batch)

                    # Check if session expired
                    if self.is_expired():
                        logger.info(f"Session {self.session_id}: Expired, stopping collector")
                        self.stop()
                        break

                    time.sleep(1)  # Poll every second

                except Exception as e:
                    logger.error(f"Session {self.session_id}: Batch collector error: {e}")
                    time.sleep(1)

            logger.info(f"Session {self.session_id}: Batch collector thread stopped")

        collector_thread = threading.Thread(target=collect_batches, daemon=True)
        collector_thread.start()

    def stop(self) -> None:
        """Stop the delegation session and clean up resources."""
        logger.info(f"Session {self.session_id}: Stopping session")
        self.status = DelegationStatus.STOPPED

        if self.controller:
            try:
                self.controller.stop()
                logger.info(f"Session {self.session_id}: Controller stopped")
            except Exception as e:
                logger.error(f"Session {self.session_id}: Error stopping controller: {e}")

    def get_gpu_count(self) -> int:
        """
        Get number of GPU groups in controller.

        Returns:
            Number of GPU groups, or 0 if controller not created
        """
        if self.controller:
            return len(self.controller.controllers)
        return 0


class DelegationManager:
    """
    Manages multiple delegation sessions on big node.

    Singleton class that handles session creation, retrieval,
    cleanup, and authentication.
    """

    def __init__(
        self,
        auth_token: str,
        max_sessions: int = 10
    ):
        """
        Initialize delegation manager.

        Args:
            auth_token: Required auth token for all requests
            max_sessions: Maximum number of concurrent sessions
        """
        self.auth_token = auth_token
        self.max_sessions = max_sessions
        self.sessions: Dict[str, DelegationSession] = {}
        self.lock = threading.Lock()

        # Start cleanup thread
        self._start_cleanup_thread()

        logger.info(
            f"DelegationManager initialized: "
            f"max_sessions={max_sessions}"
        )

    def validate_token(self, token: str) -> bool:
        """
        Validate auth token.

        Args:
            token: Token to validate

        Returns:
            True if token is valid
        """
        return token == self.auth_token

    def create_session(
        self,
        request: DelegationStartRequest
    ) -> DelegationSession:
        """
        Create new delegation session.

        Args:
            request: Delegation start request

        Returns:
            Created delegation session

        Raises:
            ValueError: If token invalid or max sessions reached
        """
        # Validate token
        if not self.validate_token(request.auth_token):
            raise ValueError("Invalid auth token")

        with self.lock:
            # Check session limit
            active_sessions = [
                s for s in self.sessions.values()
                if s.status in [DelegationStatus.INITIALIZING, DelegationStatus.GENERATING]
            ]
            if len(active_sessions) >= self.max_sessions:
                raise ValueError(
                    f"Maximum sessions ({self.max_sessions}) reached. "
                    f"Active sessions: {len(active_sessions)}"
                )

            # Create session
            session_id = str(uuid.uuid4())
            session = DelegationSession(
                session_id=session_id,
                small_node_params=request,
            )

            self.sessions[session_id] = session
            logger.info(
                f"Created session {session_id} for node_id={request.node_id}, "
                f"active sessions: {len(active_sessions) + 1}"
            )

        # Start generation (outside lock to avoid blocking)
        try:
            session.start_generation()
        except Exception as e:
            # Remove session if start failed
            with self.lock:
                self.sessions.pop(session_id, None)
            raise

        return session

    def get_session(self, session_id: str) -> Optional[DelegationSession]:
        """
        Get session by ID.

        Args:
            session_id: Session identifier

        Returns:
            DelegationSession if found, None otherwise
        """
        with self.lock:
            return self.sessions.get(session_id)

    def get_batches(
        self,
        session_id: str,
        auth_token: str
    ) -> List[ProofBatch]:
        """
        Get batches from session.

        Args:
            session_id: Session identifier
            auth_token: Auth token

        Returns:
            List of batches

        Raises:
            ValueError: If token invalid or session not found
        """
        if not self.validate_token(auth_token):
            raise ValueError("Invalid auth token")

        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        return session.get_batches()

    def stop_session(
        self,
        session_id: str,
        auth_token: str
    ) -> DelegationSession:
        """
        Stop session.

        Args:
            session_id: Session identifier
            auth_token: Auth token

        Returns:
            Stopped delegation session

        Raises:
            ValueError: If token invalid or session not found
        """
        if not self.validate_token(auth_token):
            raise ValueError("Invalid auth token")

        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.stop()
        logger.info(f"Stopped session {session_id}")
        return session

    def cleanup_expired(self) -> int:
        """
        Remove expired sessions.

        Returns:
            Number of sessions cleaned up
        """
        with self.lock:
            expired = [
                sid for sid, session in self.sessions.items()
                if session.is_expired()
            ]

            for sid in expired:
                session = self.sessions.pop(sid)
                try:
                    session.stop()
                except Exception as e:
                    logger.error(f"Error stopping expired session {sid}: {e}")

            if expired:
                logger.info(f"Cleaned up {len(expired)} expired sessions: {expired}")

            return len(expired)

    def _start_cleanup_thread(self) -> None:
        """Start background thread for periodic cleanup."""
        def cleanup_loop():
            while True:
                try:
                    self.cleanup_expired()
                except Exception as e:
                    logger.error(f"Cleanup thread error: {e}")
                time.sleep(60)  # Cleanup every minute

        cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        cleanup_thread.start()
        logger.info("Cleanup thread started")

    def get_status(self) -> Dict:
        """
        Get manager status.

        Returns:
            Dictionary with status information
        """
        with self.lock:
            total_sessions = len(self.sessions)
            active_sessions = len([
                s for s in self.sessions.values()
                if s.status == DelegationStatus.GENERATING
            ])

            return {
                "total_sessions": total_sessions,
                "active_sessions": active_sessions,
                "max_sessions": self.max_sessions,
                "sessions": {
                    sid: {
                        "status": session.status.value,
                        "node_id": session.small_node_params.node_id,
                        "gpu_count": session.get_gpu_count(),
                        "total_generated": session.total_generated,
                        "created_at": session.created_at,
                        "age_seconds": time.time() - session.created_at,
                    }
                    for sid, session in self.sessions.items()
                }
            }
