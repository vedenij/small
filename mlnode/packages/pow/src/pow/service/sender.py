import time
import requests
from requests.exceptions import RequestException
from typing import List
from multiprocessing import Process, Queue, Event

from pow.data import (
    ProofBatch,
    ValidatedBatch,
    InValidation,
)
from pow.compute.controller import (
    Controller,
    Phase,
)
from common.logger import create_logger

logger = create_logger(__name__)


class Sender(Process):
    def __init__(
        self,
        url: str,
        generation_queue: Queue,
        validation_queue: Queue,
        phase: Phase,
        r_target: float,
        fraud_threshold: float,
    ):
        super().__init__()
        self.url = url
        self.phase = phase
        self.generation_queue = generation_queue
        self.validation_queue = validation_queue
        self.in_validation_queue = Queue()
        self.r_target = r_target
        self.fraud_threshold = fraud_threshold

        self.in_validation: List[InValidation] = []
        self.generated_not_sent: List[ProofBatch] = []
        self.validated_not_sent: List[ValidatedBatch] = []
        self.stop_event = Event()

    def _send_generated(self):
        if not self.generated_not_sent:
            return

        failed_batches = []

        for batch in self.generated_not_sent:
            try:
                num_nonces = len(batch.nonces)
                logger.info(
                    f"Sending generated batch to {self.url}: "
                    f"node_id={batch.node_id}, block_height={batch.block_height}, "
                    f"nonces={num_nonces}"
                )
                response = requests.post(
                    f"{self.url}/generated",
                    json=batch.__dict__,
                )
                response.raise_for_status()
                logger.info(
                    f"✓ Successfully sent batch: node_id={batch.node_id}, "
                    f"nonces={num_nonces}, status={response.status_code}"
                )
            except RequestException as e:
                failed_batches.append(batch)
                logger.error(
                    f"✗ Failed to send batch: node_id={batch.node_id}, "
                    f"nonces={len(batch.nonces)}, error={e}"
                )

        self.generated_not_sent = failed_batches

    def _send_validated(self):
        if not self.validated_not_sent:
            return

        failed_batches = []

        for batch in self.validated_not_sent:
            try:
                logger.info(f"Sending validated batch to {self.url}")
                response = requests.post(
                    f"{self.url}/validated",
                    json=batch.__dict__,
                )
                response.raise_for_status()
                logger.info("Successfully sent validated batch")
            except RequestException as e:
                failed_batches.append(batch)
                logger.error(f"Error sending validated batch to {self.url}: {e}")

        self.validated_not_sent = failed_batches

    def _get_generated(self) -> ProofBatch:
        # Get all batches from queue
        batches_from_queue = Controller.get_from_queue(self.generation_queue)

        # In delegation mode, batches come pre-formed from big node
        # Just merge them together
        if len(batches_from_queue) == 0:
            return ProofBatch.empty()

        return ProofBatch.merge(batches_from_queue)

    def _get_validated(self) -> List[ValidatedBatch]:
        batches = Controller.get_from_queue(self.validation_queue)
        in_validation = self._get_in_validation()
        for batch in batches:
            for in_val in in_validation:
                in_val.process(batch)

        in_validation_ready = [
            in_val.validated(self.r_target, self.fraud_threshold)
            for in_val in in_validation
            if in_val.is_ready()
        ]
        return in_validation_ready

    def _get_in_validation(self) -> List[InValidation]:
        batches = Controller.get_from_queue(self.in_validation_queue)
        batches = [
            InValidation(batch)
            for batch in batches
        ]
        self.in_validation.extend(batches)
        return self.in_validation

    def run(self):
        logger.info("Sender started")
        while not self.stop_event.is_set():
            # Delegation mode: phase is None, always send generated batches
            if self.phase is None:
                generated = self._get_generated()
                logger.debug(f"[Delegation] Got generated batch with {len(generated.nonces) if generated else 0} nonces")
                if generated and len(generated.nonces) > 0:
                    self.generated_not_sent.append(generated)
                    logger.debug(f"[Delegation] Added to send queue, total pending: {len(self.generated_not_sent)}")
                self._send_generated()

            elif self.phase.value == Phase.GENERATE:
                generated = self._get_generated()
                if len(generated) > 0:
                    self.generated_not_sent.append(generated)
                self._send_generated()

            elif self.phase.value == Phase.VALIDATE:
                self.validated_not_sent.extend(self._get_validated())
                self.in_validation = [
                    b for b in self.in_validation
                    if not b.is_ready()
                ]
                self._send_validated()

            time.sleep(5)
        logger.info("Sender stopped")

    def stop(self):
        self.stop_event.set()
