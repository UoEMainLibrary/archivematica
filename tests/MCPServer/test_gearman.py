import math
import uuid
from unittest import mock

import gearman
import pytest

from archivematica.MCPServer.server.jobs import Job
from archivematica.MCPServer.server.tasks import GearmanTaskBackend
from archivematica.MCPServer.server.tasks import Task


class MockJob(Job):
    def __init__(self, *args, **kwargs):
        self.name = kwargs.pop("name", "")
        super().__init__(*args, **kwargs)

    def run(self, *args, **kwargs):
        pass


@pytest.fixture
def simple_job(request):
    return MockJob(mock.Mock(), mock.Mock(), mock.Mock(), name="test_job_name")


@pytest.fixture
def simple_task(request):
    return Task(
        "a argument string",
        "/tmp/stdoutfile",
        "/tmp/stderrfile",
        {r"%relativeLocation%": "testfile"},
        wants_output=False,
    )


def format_gearman_request(tasks):
    request = {"tasks": {}}
    for task in tasks:
        task_uuid = str(task.uuid)
        request["tasks"][task_uuid] = {
            "uuid": task_uuid,
            "createdDate": task.start_timestamp,
            "arguments": task.arguments,
            "wants_output": task.wants_output,
        }

    return request


def format_gearman_response(task_results):
    """Accepts task results as a tuple of (uuid, result_dict)."""
    response = {"task_results": {}}
    for task_uuid, task_data in task_results:
        task_uuid = str(task_uuid)
        response["task_results"][task_uuid] = task_data

    return response


@mock.patch(
    "archivematica.MCPServer.server.tasks.backends.gearman_backend.MCPGearmanClient"
)
@mock.patch(
    "archivematica.MCPServer.server.tasks.GearmanTaskBackend.TASK_BATCH_SIZE", 1
)
@mock.patch(
    "archivematica.MCPServer.server.tasks.backends.gearman_backend.Task.bulk_log"
)
def test_gearman_task_submission(bulk_log, mock_client, simple_job, simple_task):
    backend = GearmanTaskBackend()
    backend.submit_task(simple_job, simple_task)

    task_data = format_gearman_request([simple_task])

    submit_job_kwargs = mock_client.return_value.submit_job.call_args[1]

    assert submit_job_kwargs["task"] == simple_job.name.encode()
    assert submit_job_kwargs["data"] == task_data
    try:
        uuid.UUID(submit_job_kwargs["unique"].decode())
    except ValueError:
        pytest.fail("Expected unique to be a valid UUID.")
    assert submit_job_kwargs["wait_until_complete"] is False
    assert submit_job_kwargs["background"] is False
    assert submit_job_kwargs["max_retries"] == GearmanTaskBackend.MAX_RETRIES


@mock.patch(
    "archivematica.MCPServer.server.tasks.backends.gearman_backend.MCPGearmanClient"
)
@mock.patch(
    "archivematica.MCPServer.server.tasks.backends.gearman_backend.Task.bulk_log"
)
def test_gearman_task_result_success(bulk_log, mock_client, simple_job, simple_task):
    backend = GearmanTaskBackend()

    mock_gearman_job = mock.Mock()
    job_request = gearman.job.GearmanJobRequest(
        mock_gearman_job, background=True, max_attempts=0
    )

    def mock_jobs_completed(*args):
        job_request.state = gearman.JOB_COMPLETE
        job_request.result = format_gearman_response(
            [
                (
                    simple_task.uuid,
                    {
                        "exitCode": 0,
                        "stdout": "stdout example",
                        "stderr": "stderr example",
                    },
                )
            ]
        )

        return [job_request]

    mock_client.return_value.submit_job.return_value = job_request
    mock_client.return_value.wait_until_any_job_completed.side_effect = (
        mock_jobs_completed
    )

    backend.submit_task(simple_job, simple_task)
    results = list(backend.wait_for_results(simple_job))

    assert len(results) == 1

    mock_client.return_value.submit_job.assert_called_once()
    mock_client.return_value.wait_until_any_job_completed.assert_called_once()

    task_result = results[0]
    assert task_result.exit_code == 0
    assert task_result.stdout == "stdout example"
    assert task_result.stderr == "stderr example"
    assert task_result.done is True


@mock.patch(
    "archivematica.MCPServer.server.tasks.backends.gearman_backend.MCPGearmanClient"
)
@mock.patch(
    "archivematica.MCPServer.server.tasks.backends.gearman_backend.Task.bulk_log"
)
def test_gearman_task_result_error(bulk_log, mock_client, simple_job, simple_task):
    backend = GearmanTaskBackend()

    mock_gearman_job = mock.Mock()
    job_request = gearman.job.GearmanJobRequest(
        mock_gearman_job, background=True, max_attempts=0
    )

    def mock_jobs_completed(*args):
        job_request.state = gearman.JOB_FAILED
        job_request.exception = Exception("Error!")

        return [job_request]

    mock_client.return_value.submit_job.return_value = job_request
    mock_client.return_value.wait_until_any_job_completed.side_effect = (
        mock_jobs_completed
    )

    backend.submit_task(simple_job, simple_task)
    results = list(backend.wait_for_results(simple_job))

    assert len(results) == 1

    mock_client.return_value.submit_job.assert_called_once()
    mock_client.return_value.wait_until_any_job_completed.assert_called_once()

    task_result = results[0]
    assert task_result.exit_code == 1
    assert task_result.done is True


@pytest.mark.parametrize(
    "reverse_result_order", (False, True), ids=["regular", "reversed"]
)
@mock.patch(
    "archivematica.MCPServer.server.tasks.backends.gearman_backend.MCPGearmanClient"
)
@mock.patch.object(GearmanTaskBackend, "TASK_BATCH_SIZE", 2)
@mock.patch(
    "archivematica.MCPServer.server.tasks.backends.gearman_backend.Task.bulk_log"
)
def test_gearman_multiple_batches(
    bulk_log, mock_client, simple_job, simple_task, reverse_result_order
):
    tasks = []
    for i in range(5):
        task = Task(
            f"a argument string {i}",
            "/tmp/stdoutfile",
            "/tmp/stderrfile",
            {r"%relativeLocation%": "testfile"},
            wants_output=False,
        )
        tasks.append(task)

    backend = GearmanTaskBackend()

    job_requests = []
    for _ in range(3):
        mock_gearman_job = mock.Mock()
        job_request = gearman.job.GearmanJobRequest(
            mock_gearman_job, background=True, max_attempts=0
        )
        job_requests.append(job_request)

    def mock_get_job_statuses(*args):
        """Complete one batch per call, either in regular or reverse order."""
        status_requests = list(job_requests)
        if reverse_result_order:
            status_requests = reversed(status_requests)
            task_batches = [tasks[4:], tasks[2:4], tasks[:2]]
        else:
            task_batches = [tasks[:2], tasks[2:4], tasks[4:]]

        for index, job_request in enumerate(status_requests):
            if job_request.state != gearman.JOB_COMPLETE:
                job_request.state = gearman.JOB_COMPLETE
                job_request.result = format_gearman_response(
                    [
                        (
                            task.uuid,
                            {
                                "exitCode": 0,
                                "stdout": f"stdout example {index}",
                                "stderr": f"stderr example {index}",
                            },
                        )
                        for task in task_batches[index]
                    ]
                )
                break

        return job_requests

    mock_client.return_value.submit_job.side_effect = job_requests
    mock_client.return_value.wait_until_any_job_completed.side_effect = (
        mock_get_job_statuses
    )

    for task in tasks:
        backend.submit_task(simple_job, task)
    results = list(backend.wait_for_results(simple_job))

    expected_batch_count = int(math.ceil(5 / backend.TASK_BATCH_SIZE))
    expected_first_result = tasks[-1] if reverse_result_order else tasks[0]

    assert len(results) == 5
    assert results[0] is expected_first_result
    assert mock_client.return_value.submit_job.call_count == expected_batch_count
    assert mock_client.return_value.wait_until_any_job_completed.call_count == len(
        job_requests
    )
