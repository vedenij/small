import os
from typing import Optional, Union
from enum import Enum
from pydantic import BaseModel

from pow.models.utils import Params
from pow.compute.controller import ParallelController
from pow.service.delegation.client import DelegationClient
from pow.service.delegation.types import DelegationStartRequest
from common.logger import create_logger
from pow.service.sender import Sender
from pow.compute.utils import Phase
from common.manager import IManager


class PowState(Enum):
    IDLE = "IDLE"
    NO_CONTROLLER = "NOT_LOADED"
    LOADING = "LOADING"
    GENERATING = "GENERATING"
    VALIDATING = "VALIDATING"
    STOPPED = "STOPPED"
    MIXED = "MIXED"


class PowInitRequest(BaseModel):
    node_id: int = -1
    node_count: int = -1
    block_hash: str
    block_height: int
    public_key: str
    batch_size: int
    r_target: float
    fraud_threshold: float
    params: Params = Params()
    # Delegation parameters (optional)
    delegation_url: Optional[str] = None  # e.g., "http://big-node:9090"
    delegation_auth_token: Optional[str] = None


class PowInitRequestUrl(PowInitRequest):
    url: str


logger = create_logger(__name__)


class PowManager(IManager):
    def __init__(self):
        super().__init__()
        self.pow_controller: Optional[Union[ParallelController, DelegationClient]] = None
        self.local_controller: Optional[ParallelController] = None  # For validation in delegation mode
        self.pow_sender: Optional[Sender] = None
        self.init_request: Optional[PowInitRequest] = None
        self._using_delegation: bool = False

    def switch_to_pow(
        self,
        init_request: PowInitRequest
    ):
        if self.pow_controller is not None:
            logger.info("Stopping PoW controller")
            self.stop()
        
        self.init(init_request)
        self.start()

    def init(
        self,
        init_request: PowInitRequest
    ):
        self.init_request = init_request

        # Check if delegation mode is explicitly enabled
        delegation_enabled = os.getenv("DELEGATION_ENABLED", "0") == "1"

        if delegation_enabled:
            # Read delegation parameters from environment (priority) or request (fallback)
            delegation_url = os.getenv("DELEGATION_URL") or init_request.delegation_url
            delegation_auth_token = os.getenv("DELEGATION_AUTH_TOKEN") or init_request.delegation_auth_token

            # Validate that required parameters are set
            if not delegation_url:
                raise ValueError(
                    "DELEGATION_ENABLED=1 but DELEGATION_URL is not set. "
                    "Please set DELEGATION_URL in config.env"
                )
            if not delegation_auth_token:
                raise ValueError(
                    "DELEGATION_ENABLED=1 but DELEGATION_AUTH_TOKEN is not set. "
                    "Please set DELEGATION_AUTH_TOKEN in config.env"
                )

            logger.info(
                f"Initializing in DELEGATION mode: "
                f"delegation_url={delegation_url}"
            )

            # Create delegation request
            delegation_request = DelegationStartRequest(
                node_id=init_request.node_id,
                node_count=init_request.node_count,
                block_hash=init_request.block_hash,
                block_height=init_request.block_height,
                public_key=init_request.public_key,
                batch_size=init_request.batch_size,
                r_target=init_request.r_target,
                fraud_threshold=init_request.fraud_threshold,
                params=init_request.params,
                auth_token=delegation_auth_token,
            )

            # Create delegation client for generation
            self.pow_controller = DelegationClient(
                big_node_url=delegation_url,
                delegation_request=delegation_request,
            )
            self._using_delegation = True

            # Create LOCAL controller for validation (on small node's GPU)
            logger.info("Creating local ParallelController for validation")
            self.local_controller = ParallelController(
                params=init_request.params,
                block_hash=init_request.block_hash,
                block_height=init_request.block_height,
                public_key=init_request.public_key,
                node_id=init_request.node_id,
                node_count=init_request.node_count,
                batch_size=init_request.batch_size,
                r_target=init_request.r_target,
                devices=None,  # Auto-detect local GPUs
            )

            # Create sender (uses delegation client for generation, local controller for validation)
            self.pow_sender = Sender(
                url=init_request.url,
                generation_queue=self.pow_controller.generated_batch_queue,
                validation_queue=self.local_controller.validated_batch_queue,
                phase=self.local_controller.phase,  # Use local controller's phase
                r_target=init_request.r_target,
                fraud_threshold=init_request.fraud_threshold,
                using_delegation=True,  # Enable hybrid delegation mode
            )

        else:
            logger.info("Initializing in LOCAL mode")

            # Create local ParallelController
            self.pow_controller = ParallelController(
                params=init_request.params,
                block_hash=init_request.block_hash,
                block_height=init_request.block_height,
                public_key=init_request.public_key,
                node_id=init_request.node_id,
                node_count=init_request.node_count,
                batch_size=init_request.batch_size,
                r_target=init_request.r_target,
                devices=None,
            )
            self._using_delegation = False

            # Create sender (uses controller's queues)
            self.pow_sender = Sender(
                url=init_request.url,
                generation_queue=self.pow_controller.generated_batch_queue,
                validation_queue=self.pow_controller.validated_batch_queue,
                phase=self.pow_controller.phase,
                r_target=self.pow_controller.r_target,
                fraud_threshold=init_request.fraud_threshold,
            )

    def _start(self):
        if self.pow_controller is None:
            raise Exception("PoW not initialized")

        if self.pow_controller.is_running():
            raise Exception("PoW is already running")

        logger.info(f"Starting controller with params: {self.init_request}")

        # Start delegation client (for generation)
        self.pow_controller.start()

        # In delegation mode, also start local controller (for validation)
        if self._using_delegation and self.local_controller:
            logger.info("Starting local controller for validation")
            self.local_controller.start()

        self.pow_sender.start()

    def get_pow_status(self) -> dict:
        if self.pow_controller is None:
            return {
                "status": PowState.NO_CONTROLLER,
            }

        # Handle delegation mode (DelegationClient doesn't have phase/is_model_initialized)
        if self._using_delegation:
            return {
                "status": PowState.GENERATING if self.pow_controller.is_running() else PowState.IDLE,
                "is_model_initialized": True,  # Big node handles model
                "delegation_mode": True,
                "gpu_count": self.pow_controller.get_gpu_count(),
            }

        # Handle local mode (ParallelController)
        phase = self.phase_to_state(self.pow_controller.phase.value)
        loading = not self.pow_controller.is_model_initialized()
        if loading and phase == PowState.IDLE:
            phase = PowState.LOADING
        return {
            "status": phase,
            "is_model_initialized": not loading,
            "delegation_mode": False,
        }

    def _stop(self):
        # Stop delegation client (for generation)
        self.pow_controller.stop()

        # In delegation mode, also stop local controller (for validation)
        if self._using_delegation and self.local_controller:
            logger.info("Stopping local controller")
            self.local_controller.stop()

        # Stop sender
        self.pow_sender.stop()
        self.pow_sender.join(timeout=5)

        if self.pow_sender.is_alive():
            logger.warning("Sender process did not stop within the timeout period")

        self.pow_controller = None
        self.local_controller = None
        self.pow_sender = None
        self.init_request = None

        # Clean GPU memory after stopping PoC
        self._cleanup_gpu()

    @staticmethod
    def phase_to_state(phase: Phase) -> PowState:
        if phase == Phase.IDLE:
            return PowState.IDLE
        elif phase == Phase.GENERATE:
            return PowState.GENERATING
        elif phase == Phase.VALIDATE:
            return PowState.VALIDATING
        else:
            return PowState.IDLE

    def is_running(self) -> bool:
        if self.pow_controller is None:
            return False
        # In delegation mode, also check local controller if it's supposed to be running
        if self._using_delegation and self.local_controller:
            # Both controllers should be running
            return self.pow_controller.is_running() and self.local_controller.is_running()
        return self.pow_controller.is_running()

    def _is_healthy(self) -> bool:
        if self.pow_controller is None:
            return False
        # In delegation mode, check both controllers
        if self._using_delegation and self.local_controller:
            return self.pow_controller.is_alive() and self.local_controller.is_alive()
        return self.pow_controller.is_alive()

    def _cleanup_gpu(self, target_free_mib=41000, timeout=10.0):
        """
        Clean GPU memory after PoC stops, before vLLM starts.
        Waits for at least 41000 MiB (40GB) to be free for qwen model.
        Uses active waiting with 100ms checks for maximum efficiency.
        """
        import torch
        import gc
        import time

        logger.info("Cleaning GPU memory after PoC...")

        # Destroy PyTorch Distributed process group (NCCL) if exists
        try:
            import torch.distributed as dist
            if dist.is_initialized():
                logger.info("Destroying PyTorch Distributed process group...")
                dist.destroy_process_group()
        except Exception as e:
            logger.debug(f"Could not destroy process group: {e}")

        # Clear CUDA cache and IPC memory
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()

        # Python garbage collection
        gc.collect()

        # Wait for GPU memory to be released (active waiting)
        if torch.cuda.is_available():
            logger.info(f"Waiting for at least {target_free_mib} MiB GPU memory to be free...")
            start_time = time.time()

            while time.time() - start_time < timeout:
                try:
                    free_bytes, _ = torch.cuda.mem_get_info(0)
                    free_mib = free_bytes / (1024**2)

                    if free_mib >= target_free_mib:
                        elapsed = time.time() - start_time
                        logger.info(f"GPU memory released: {free_mib:.0f} MiB free (took {elapsed:.1f}s)")
                        return

                    time.sleep(0.1)  # Check every 100ms for max efficiency
                except Exception as e:
                    logger.warning(f"Error checking GPU memory: {e}")
                    break

            # Timeout reached
            try:
                free_bytes, _ = torch.cuda.mem_get_info(0)
                free_mib = free_bytes / (1024**2)
                logger.warning(f"Timeout after {timeout}s. Current free: {free_mib:.0f} MiB")
            except:
                pass
        else:
            logger.info("GPU cleanup complete (no CUDA available)")
