import uuid

from django.test import TestCase

from archivematica.dashboard.fpr.models import IDCommand
from archivematica.dashboard.fpr.utils import get_revision_descendants


class TestUtils(TestCase):
    def test_get_revision_descendants(self):
        cmd1 = IDCommand.objects.create(uuid="37f3bd7c-bb24-4899-b7c4-785ff1c764ac")
        cmd2 = IDCommand(description="Foobar")
        cmd2.save(replacing=cmd1)

        descendants = get_revision_descendants(IDCommand, cmd1.uuid, [123])
        self.assertEqual(descendants, [123, cmd2])

        with self.assertRaises(IDCommand.DoesNotExist):
            descendants = get_revision_descendants(
                IDCommand, uuid.UUID("aa5ccdbd-7ede-43f9-8752-56b5ce1c0bdb"), []
            )
