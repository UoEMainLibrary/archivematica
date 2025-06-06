"""Worker pool.

WorkerPool is the core of MCPClient, a pool of system processes that are
independent Gearman workers. Only one process in the pool will handle tasks
unless marked as safe for concurrent instances.

The pool ensures that processes are replaced when they fail or exit. The max.
number or processes allowed in the pool can be established with the ``workers``
setting. Workers can also be programmed to perform a limited number of tasks
with ``max_tasks_per_child``, which is useful to free resources held.

Workers log events into a shared queue while the pool runs a background thread
(log listener) that listens to the queue and writes the events safely.
"""

import logging
import logging.handlers
import multiprocessing
import threading
import time
from multiprocessing.synchronize import Event
from types import ModuleType
from typing import Optional
from typing import Protocol
from typing import TypeVar

import django

django.setup()

from django import db
from django.conf import settings

from archivematica.MCPClient.client import loader
from archivematica.MCPClient.client import metrics
from archivematica.MCPClient.client.gearman import MCPGearmanWorker

T = TypeVar("T")


class QueueLike(Protocol[T]):
    def get(self) -> T: ...
    def put_nowait(self, item: T) -> None: ...


LogQueue = QueueLike[logging.LogRecord]

# This is how the return value of the `_get_worker_init_args` method looks
# below:
# [
#     (
#         (
#             "<multiprocessing.queues.Queue object at 0x7609ba4badf0>",
#             [
#                 "archivematicaclamscan_v0.0",
#                 "examinecontents_v0.0",
#                 "identifyfileformat_v0.0",
#                 "transcribefile_v0.0",
#                 "characterizefile_v0.0",
#             ],
#         ),
#         {
#             "shutdown_event": "<multiprocessing.synchronize.Event object at 0x7609ba454340>"
#         },
#     ),
#     ...
# ]
WorkerInitArgs = list[tuple[tuple[LogQueue, list[str]], dict[str, Event]]]

logger = logging.getLogger("archivematica.mcp.client.worker")


def run_gearman_worker(
    log_queue: LogQueue,
    client_scripts: list[str],
    shutdown_event: Optional[Event] = None,
) -> None:
    """Target function executed by child processes in the pool."""
    # Set up logging, as we're in a new process now.
    logger = logging.getLogger("archivematica.mcp.client")
    logger.setLevel(logging.DEBUG)
    queue_handler = logging.handlers.QueueHandler(log_queue)
    logger.addHandler(queue_handler)

    process_id = multiprocessing.current_process().pid
    gearman_hosts = [settings.GEARMAN_SERVER]
    max_jobs_to_process = settings.MAX_TASKS_PER_CHILD

    # Reject connections of the parent, this process will have its own.
    db.connections.close_all()

    worker = MCPGearmanWorker(
        gearman_hosts,
        client_scripts,
        shutdown_event=shutdown_event,
        max_jobs_to_process=max_jobs_to_process,
    )
    logger.debug("Worker process %s starting", process_id)
    worker.work()
    logger.debug("Worker process %s exiting", process_id)


class WorkerPool:
    # Delay in the maintenance loop until workers are checked and restarted.
    WORKER_RESTART_DELAY = 1.0

    def __init__(self) -> None:
        self.log_queue: LogQueue = multiprocessing.Queue()
        self.shutdown_event = multiprocessing.Event()
        self.workers: list[multiprocessing.Process] = []
        self.job_modules = loader.load_job_modules(settings.CLIENT_MODULES_FILE)
        self.worker_function = run_gearman_worker

        # The max. number of workers is established by the ``workers`` setting,
        # but the final number of workers may be lower (but not higher) meeting
        # the demand of client modules and ``concurrent_instances``.`
        workers_required = self._get_script_workers_required(self.job_modules)
        self.pool_size = min(settings.WORKERS, max(workers_required.values()))
        self._worker_init_args = self._get_worker_init_args(workers_required)

        self.pool_maintainance_thread: Optional[threading.Thread] = None
        self.logging_listener: Optional[logging.handlers.QueueListener] = None

    def start(self) -> None:
        self.logging_listener = logging.handlers.QueueListener(
            self.log_queue,
            *logger.handlers,
            respect_handler_level=True,
        )
        self.logging_listener.start()

        for i in range(self.pool_size - len(self.workers)):
            worker = self._start_worker(i)
            self.workers.append(worker)

        self.pool_maintainance_thread = threading.Thread(target=self._maintain_pool)
        self.pool_maintainance_thread.daemon = True
        self.pool_maintainance_thread.start()

    def stop(self) -> None:
        self.shutdown_event.set()
        if self.pool_maintainance_thread is not None:
            self.pool_maintainance_thread.join()

        for worker in self.workers:
            if worker.is_alive():
                worker.join(0.1)

        for worker in self.workers:
            if worker.is_alive():
                worker.terminate()

        for worker in self.workers:
            if not worker.is_alive():
                metrics.worker_exit(worker.pid)

        if self.logging_listener is not None:
            self.logging_listener.stop()

    def _get_script_workers_required(
        self, job_modules: dict[str, Optional[ModuleType]]
    ) -> dict[str, int]:
        workers_required = {}
        for client_script, module in job_modules.items():
            concurrency = loader.get_module_concurrency(module)
            workers_required[client_script] = concurrency

        return workers_required

    # Use Queue[logging.LogRecord] instead of Any
    def _get_worker_init_args(
        self, script_workers_required: dict[str, int]
    ) -> WorkerInitArgs:
        # Don't mutate the argument
        script_workers_required = script_workers_required.copy()
        init_scripts: list[list[str]] = []

        for i in range(self.pool_size):
            init_scripts.append([])
            for script_name, workers_remaining in script_workers_required.items():
                if workers_remaining > 0:
                    init_scripts[i].append(script_name)
                    script_workers_required[script_name] -= 1

        return [
            (
                (self.log_queue, worker_init_scripts),
                {
                    "shutdown_event": self.shutdown_event,
                },
            )
            for worker_init_scripts in init_scripts
        ]

    def _maintain_pool(self) -> None:
        """Run as a loop in another thread; we check the pool size and spin up
        new workers as required.
        """
        while not self.shutdown_event.is_set():
            self._restart_exited_workers()
            time.sleep(self.WORKER_RESTART_DELAY)

    def _restart_exited_workers(self) -> bool:
        """Restart any worker processes which have exited due to reaching
        their specified lifetime.  Returns True if any workers were restarted.
        """
        restarted = False
        for index, worker in enumerate(self.workers):
            if worker.exitcode is not None:
                metrics.worker_exit(worker.pid)
                worker.join()
                restarted = True
                self.workers[index] = self._start_worker(index)

        return restarted

    def _start_worker(self, index: int) -> multiprocessing.Process:
        """Start the new worker in a separate process."""
        worker_args, worker_kwargs = self._worker_init_args[index]
        worker = multiprocessing.Process(
            name=f"MCPClientWorker-{index}",
            target=self.worker_function,
            args=worker_args,
            kwargs=worker_kwargs,
        )
        worker.daemon = False
        worker.start()

        return worker
