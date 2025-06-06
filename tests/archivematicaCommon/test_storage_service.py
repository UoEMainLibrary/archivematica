"""Test Storage Service

Tests for the Archivematica Common Storage Service helpers.
"""

import json
from unittest import mock

import pytest
from requests import Response

from archivematica.archivematicaCommon.storageService import (
    location_description_from_slug,
)
from archivematica.archivematicaCommon.storageService import (
    retrieve_storage_location_description,
)


def mock_response(status_code, content_type, content):
    response = Response()
    response.status_code = status_code
    response.headers["content-type"] = content_type
    response.status = "Mocked status value"
    response._content = json.dumps(content).encode("utf8")
    return response


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status_code,content_type,expected_result",
    [
        (200, "application/json", {"description": "a description", "path": "/a/path/"}),
        (200, "application/gzip", {}),
        (204, "x-application/mocked", {}),
        (400, "x-application/mocked", {}),
        (500, "x-application/mocked", {}),
        (0, "", {}),
    ],
)
@mock.patch("archivematica.archivematicaCommon.storageService._storage_service_url")
@mock.patch("requests.Session.get")
def test_location_desc_from_slug(
    get, _storage_service_url, status_code, content_type, expected_result
):
    """Test location description from slug

    Rudimentary test to ensure that we're returning something that
    implements .get() for any potential return from this function. And
    that we get something sensible for unexpected status codes.
    """

    get.return_value = mock_response(status_code, content_type, expected_result)
    res = location_description_from_slug("mock_uri")
    assert res == expected_result, f"Unexpected result for status test: {status_code}"


@pytest.mark.parametrize(
    "slug,return_value,expected_result",
    [
        (
            "/api/v2/location/3e796bef-0d56-4471-8700-eeb256859811/",
            {"description": "a description"},
            "a description",
        ),
        (
            "/api/v2/location/default/AS/",
            {"description": None, "path": "/path/one/"},
            "/path/one/",
        ),
        (
            "/api/v2/location/e0a9558c-ae00-4e39-886d-2a38bba98c72/",
            {"path": "/path/two"},
            "/path/two",
        ),
        (
            "/api/v2/location/e0a9558c-ae00-4e39-886d-2a38bba98c72/",
            {"description": "a description", "path": "/path/three"},
            "a description",
        ),
        ("/api/v2/location/fd46760b-567f-4c17-a2f4-a05e79074932/", {}, ""),
    ],
)
@mock.patch(
    "archivematica.archivematicaCommon.storageService.location_description_from_slug"
)
def test_retrieve_storage_location(
    location_description_from_slug, slug, return_value, expected_result
):
    """Test retrieve storage location

    Ensure that we're able to retrieve the resource description for the
    storage service from our request to the storage service. We should
    be able to retrieve a 'description' or "path" or "" (blank string)
    in that order, depending on the response, i.e. if a user hasn't
    specified a description, we should be able to fall-back on something
    else. Likewise if we receive something unexpected.
    """
    location_description_from_slug.return_value = return_value
    res = retrieve_storage_location_description(slug)
    assert res == expected_result
