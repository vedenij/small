"""
Delegation client for small node.

Handles delegation of PoC computation to a big node.
"""

import time
import threading
import requests
from typing import Optional
from queue import Queue

from pow.data import ProofBatch
from pow.service.delegation.types import (
    DelegationStartRequest,
    DelegationStartResponse,
    DelegationBatchResponse,
    DelegationStopRequest,
    DelegationStopResponse,
    DelegationStatus,
)
from common.logger import create_logger

logger = create_logger(__name__)

# Poll interval in seconds
POLL_INTERVAL = 5


class DelegationClient:
    """
    Client for delegating PoC computation to big node.

    Mimics ParallelController interface so it can be used as a drop-in
    replacement in PowManager. Polls big node for batches and puts them
    into generated_batch_queue for Sender to consume.

    Attributes:
        big_node_url: Base URL of big node (e.g., "http://big-node:9090")
        delegation_request: Parameters for delegation
        session_id: Session ID from big node
        generated_batch_queue: Queue for generated batches (consumed by Sender)
        _running: Whether polling is active
        _poll_thread: Background thread for polling
        _lock: Thread lock for state access
    """

    def __init__(
        self,
        big_node_url: str,
        delegation_request: DelegationStartRequest,
    ):
        """
        Initialize delegation client.

        Args:
            big_node_url: Base URL of big node (e.g., "http://big-node:9090")
            delegation_request: Parameters for delegation
        """
        self.big_node_url = big_node_url.rstrip('/')
        self.delegation_request = delegation_request
        self.session_id: Optional[str] = None
        self.generated_batch_queue: Queue = Queue()

        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._gpu_count = 0

        logger.info(
            f"DelegationClient initialized: big_node_url={big_node_url}, "
            f"node_id={delegation_request.node_id}"
        )

    def start(self) -> None:
        """
        Start delegation session on big node.

        Raises:
            Exception: If session creation fails
        """
        with self._lock:
            if self._running:
                logger.warning("DelegationClient already running")
                return

            try:
                # Start session on big node
                response = requests.post(
                    f"{self.big_node_url}/api/v1/delegation/start",
                    json=self.delegation_request.model_dump(),
                    timeout=30,
                )
                response.raise_for_status()

                start_response = DelegationStartResponse(**response.json())
                self.session_id = start_response.session_id
                self._gpu_count = start_response.gpu_count

                logger.info(
                    f"Delegation session started: session_id={self.session_id}, "
                    f"gpu_count={self._gpu_count}, status={start_response.status}"
                )

                # Start polling thread
                self._running = True
                self._poll_thread = threading.Thread(
                    target=self._poll_loop,
                    daemon=True,
                )
                self._poll_thread.start()
                logger.info("Polling thread started")

            except Exception as e:
                logger.error(f"Failed to start delegation session: {e}")
                raise

    def stop(self) -> None:
        """Stop delegation session and cleanup."""
        with self._lock:
            if not self._running:
                return

            self._running = False
            logger.info("Stopping delegation client")

        # Wait for poll thread to finish
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=10)
            if self._poll_thread.is_alive():
                logger.warning("Poll thread did not stop within timeout")

        # Stop session on big node
        if self.session_id:
            try:
                stop_request = DelegationStopRequest(
                    session_id=self.session_id,
                    auth_token=self.delegation_request.auth_token,
                )
                response = requests.post(
                    f"{self.big_node_url}/api/v1/delegation/stop",
                    json=stop_request.model_dump(),
                    timeout=30,
                )
                response.raise_for_status()

                stop_response = DelegationStopResponse(**response.json())
                logger.info(
                    f"Delegation session stopped: session_id={self.session_id}, "
                    f"total_batches={stop_response.total_batches_generated}"
                )

            except Exception as e:
                logger.error(f"Failed to stop delegation session: {e}")

        self.session_id = None
        logger.info("DelegationClient stopped")

    def _poll_loop(self) -> None:
        """
        Background thread that polls big node for batches.

        Continuously polls /api/v1/delegation/batches/{session_id}
        and puts received batches into generated_batch_queue.
        """
        logger.info("Poll loop started")
        consecutive_errors = 0
        max_consecutive_errors = 10

        while self._running:
            try:
                if not self.session_id:
                    logger.error("No session_id, stopping poll loop")
                    break

                # Poll for batches
                response = requests.get(
                    f"{self.big_node_url}/api/v1/delegation/batches/{self.session_id}",
                    params={"auth_token": self.delegation_request.auth_token},
                    timeout=30,
                )
                response.raise_for_status()

                batch_response = DelegationBatchResponse(**response.json())

                # Put batches into queue
                if batch_response.batches:
                    for batch in batch_response.batches:
                        self.generated_batch_queue.put(batch)

                    logger.info(
                        f"Received {len(batch_response.batches)} batches, "
                        f"total generated: {batch_response.total_batches_generated}"
                    )

                # Check session status
                if not batch_response.session_active:
                    logger.warning(
                        f"Session no longer active: status={batch_response.status}"
                    )
                    with self._lock:
                        self._running = False
                    break

                # Reset error counter on success
                consecutive_errors = 0

            except requests.exceptions.RequestException as e:
                consecutive_errors += 1
                logger.error(
                    f"Poll error ({consecutive_errors}/{max_consecutive_errors}): {e}"
                )

                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        f"Too many consecutive errors ({consecutive_errors}), "
                        "stopping poll loop"
                    )
                    with self._lock:
                        self._running = False
                    break

            except Exception as e:
                logger.error(f"Unexpected error in poll loop: {e}")
                consecutive_errors += 1

                if consecutive_errors >= max_consecutive_errors:
                    with self._lock:
                        self._running = False
                    break

            # Sleep before next poll
            time.sleep(POLL_INTERVAL)

        logger.info("Poll loop stopped")

    def is_running(self) -> bool:
        """
        Check if delegation client is running.

        Returns:
            True if running
        """
        with self._lock:
            return self._running

    def is_alive(self) -> bool:
        """
        Check if delegation client is alive.

        Provides interface compatibility with ParallelController.
        For delegation client, alive means running.

        Returns:
            True if alive
        """
        return self.is_running()

    def start_generate(self) -> None:
        """
        Start generation phase (no-op for delegation client).

        Big node automatically starts generation when session is created.
        This method exists for interface compatibility with ParallelController.
        """
        logger.info("start_generate() called (no-op for delegation client)")

    def get_gpu_count(self) -> int:
        """
        Get number of GPUs on big node.

        Returns:
            Number of GPU groups on big node
        """
        return self._gpu_count
