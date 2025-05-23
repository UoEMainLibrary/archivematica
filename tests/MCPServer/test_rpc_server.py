import importlib.resources
import threading
import uuid
from unittest import mock

import pytest
from django.utils import timezone

from archivematica.dashboard.main import models
from archivematica.MCPServer.server import rpc_server
from archivematica.MCPServer.server import workflow


@pytest.mark.django_db
def test_approve_partial_reingest_handler():
    sip = models.SIP.objects.create(uuid=str(uuid.uuid4()))
    models.Job.objects.create(
        sipuuid=sip.pk,
        microservicegroup="Reingest AIP",
        createdtime=timezone.now(),
        currentstep=models.Job.STATUS_AWAITING_DECISION,
    )
    package_queue = mock.MagicMock()
    with open(
        importlib.resources.files("archivematica.MCPServer")
        / "assets"
        / "workflow.json"
    ) as fp:
        wf = workflow.load(fp)
    shutdown_event = threading.Event()
    shutdown_event.set()

    server = rpc_server.RPCServer(wf, shutdown_event, package_queue, None)
    server._approve_partial_reingest_handler(None, wf, {"sip_uuid": sip.pk})

    package_queue.decide.assert_called_once()
