import pathlib
import uuid
from contextlib import ExitStack as does_not_raise
from unittest import mock

import pytest
import pytest_django
from lxml import etree

from archivematica.archivematicaCommon.namespaces import NSMAP
from archivematica.dashboard.main.models import SIP
from archivematica.dashboard.main.models import DublinCore
from archivematica.dashboard.main.models import File
from archivematica.dashboard.main.models import MetadataAppliesToType
from archivematica.dashboard.main.models import SIPArrange
from archivematica.MCPClient.client.job import Job
from archivematica.MCPClient.clientScripts.create_mets_v2 import (
    createDMDIDsFromCSVMetadata,
)
from archivematica.MCPClient.clientScripts.create_mets_v2 import main


@mock.patch(
    "archivematica.MCPClient.clientScripts.create_mets_v2.createDmdSecsFromCSVParsedMetadata",
    return_value=[],
)
def test_createDMDIDsFromCSVMetadata_finds_non_ascii_paths(
    dmd_secs_creator_mock: mock.Mock,
) -> None:
    state_mock = mock.Mock(
        **{
            "CSV_METADATA": {
                "montréal": "montreal metadata",
                "dvořák": "dvorak metadata",
            }
        }
    )

    createDMDIDsFromCSVMetadata(None, "montréal", state_mock)
    createDMDIDsFromCSVMetadata(None, "toronto", state_mock)
    createDMDIDsFromCSVMetadata(None, "dvořák", state_mock)

    dmd_secs_creator_mock.assert_has_calls(
        [
            mock.call(None, "montreal metadata", state_mock),
            mock.call(None, {}, state_mock),
            mock.call(None, "dvorak metadata", state_mock),
        ]
    )


@pytest.fixture()
def objects_path(sip_directory_path: pathlib.Path) -> pathlib.Path:
    objects_path = sip_directory_path / "objects"
    objects_path.mkdir()

    return objects_path


@pytest.fixture()
def empty_dir_path(objects_path: pathlib.Path) -> pathlib.Path:
    empty_dir_path = objects_path / "empty_dir"
    empty_dir_path.mkdir()

    return empty_dir_path


@pytest.fixture()
def metadata_csv(
    sip: SIP, sip_directory_path: pathlib.Path, objects_path: pathlib.Path
) -> File:
    (objects_path / "metadata").mkdir()
    metadata_csv = objects_path / "metadata" / "metadata.csv"
    metadata_csv.write_text("Filename,dc.title\nobjects/file1,File 1")

    originallocation = "".join(
        [r"%transferDirectory%", str(metadata_csv.relative_to(sip_directory_path))],
    )
    currentlocation = "".join(
        [r"%SIPDirectory%", str(metadata_csv.relative_to(sip_directory_path))],
    )
    file_obj = File.objects.create(
        uuid=uuid.uuid4(),
        sip=sip,
        originallocation=originallocation.encode(),
        currentlocation=currentlocation.encode(),
        size=1024,
        filegrpuse="metadata",
        checksum="f0e4c2f76c58916ec258f246851bea091d14d4247a2fc3e18694461b1816e13b",
        checksumtype="sha256",
    )

    return file_obj


@pytest.fixture()
def sip_dublincore(sip: SIP) -> DublinCore:
    return DublinCore.objects.create(
        metadataappliestotype_id=MetadataAppliesToType.SIP_TYPE,
        metadataappliestoidentifier=sip.pk,
        title="Hello World Contents",
        is_part_of="23456",
        identifier="12345",
    )


@pytest.fixture()
def file_path(objects_path: pathlib.Path) -> pathlib.Path:
    file_path = objects_path / "file1"
    file_path.write_text("Hello world")

    return file_path


@pytest.fixture()
def sip_file(
    sip_file: File, sip_directory_path: pathlib.Path, file_path: pathlib.Path
) -> File:
    sip_file.originallocation = (
        f"%transferDirectory%{file_path.relative_to(sip_directory_path)}".encode()
    )
    sip_file.currentlocation = (
        f"%SIPDirectory%{file_path.relative_to(sip_directory_path)}".encode()
    )
    sip_file.save()

    return sip_file


@pytest.mark.django_db
def test_simple_mets(
    mcp_job: Job, sip_directory_path: pathlib.Path, sip: SIP, sip_file: File
) -> None:
    mets_path = sip_directory_path / f"METS.{sip.uuid}.xml"
    main(
        mcp_job,
        sipType="SIP",
        baseDirectoryPath=str(sip_directory_path),
        XMLFile=str(mets_path),
        sipUUID=sip.pk,
        includeAmdSec=False,
        createNormativeStructmap=False,
    )
    mets_xml = etree.parse(mets_path.open())
    amdsecs = mets_xml.xpath(
        ".//mets:amdSec",
        namespaces=NSMAP,
    )
    dublincore = mets_xml.xpath(
        ".//mets:xmlData/dcterms:dublincore",
        namespaces=NSMAP,
    )
    structmap_types = mets_xml.xpath(
        ".//mets:structMap/@TYPE",
        namespaces=NSMAP,
    )

    assert len(amdsecs) == 0
    assert len(dublincore) == 0
    assert len(structmap_types) == 1
    assert "physical" in structmap_types


@pytest.mark.django_db
def test_aip_mets_includes_dublincore(
    mcp_job: Job,
    sip_directory_path: pathlib.Path,
    sip: SIP,
    sip_dublincore: DublinCore,
    sip_file: File,
) -> None:
    mets_path = sip_directory_path / f"METS.{sip.uuid}.xml"
    main(
        mcp_job,
        sipType="SIP",
        baseDirectoryPath=str(sip_directory_path),
        XMLFile=str(mets_path),
        sipUUID=sip.pk,
        includeAmdSec=True,
        createNormativeStructmap=True,
    )
    mets_xml = etree.parse(mets_path.open())
    objects_div = mets_xml.xpath(
        ".//mets:div[@LABEL='objects']",
        namespaces=NSMAP,
    )
    dmdid = objects_div[0].get("DMDID")
    dublincore = mets_xml.xpath(
        ".//mets:dmdSec[@ID='" + dmdid + "']/*/mets:xmlData/dcterms:dublincore/*",
        namespaces=NSMAP,
    )

    assert len(objects_div) == 2
    assert len(dublincore) == 3
    assert dublincore[0].tag == "{http://purl.org/dc/elements/1.1/}title"
    assert dublincore[0].text == "Hello World Contents"
    assert dublincore[1].tag == "{http://purl.org/dc/elements/1.1/}identifier"
    assert dublincore[1].text == "12345"
    assert dublincore[2].tag == "{http://purl.org/dc/terms/}isPartOf"
    assert dublincore[2].text == "23456"


@pytest.mark.django_db
def test_aip_mets_includes_dublincore_via_metadata_csv(
    mcp_job: Job,
    sip_directory_path: pathlib.Path,
    sip: SIP,
    sip_file: File,
    metadata_csv: File,
) -> None:
    mets_path = sip_directory_path / f"METS.{sip.uuid}.xml"
    main(
        mcp_job,
        sipType="SIP",
        baseDirectoryPath=str(sip_directory_path),
        XMLFile=str(mets_path),
        sipUUID=sip.pk,
        includeAmdSec=True,
        createNormativeStructmap=True,
    )
    mets_xml = etree.parse(mets_path.open())
    file_div = mets_xml.xpath(
        ".//mets:div[@LABEL='file1']",
        namespaces=NSMAP,
    )
    dmdid = file_div[0].get("DMDID")
    dublincore = mets_xml.xpath(
        ".//mets:dmdSec[@ID='" + dmdid + "']/*/mets:xmlData/dcterms:dublincore/*",
        namespaces=NSMAP,
    )

    assert len(file_div) == 2
    assert len(dublincore) == 1
    assert dublincore[0].tag == "{http://purl.org/dc/elements/1.1/}title"
    assert dublincore[0].text == "File 1"


@pytest.mark.django_db
def test_aip_mets_normative_directory_structure(
    mcp_job: Job,
    sip_directory_path: pathlib.Path,
    sip: SIP,
    sip_file: File,
    metadata_csv: File,
    empty_dir_path: pathlib.Path,
) -> None:
    mets_path = sip_directory_path / f"METS.{sip.uuid}.xml"
    main(
        mcp_job,
        sipType="SIP",
        baseDirectoryPath=str(sip_directory_path),
        XMLFile=str(mets_path),
        sipUUID=sip.pk,
        includeAmdSec=True,
        createNormativeStructmap=True,
    )
    mets_xml = etree.parse(mets_path.open())
    normative_structmap = mets_xml.xpath(
        ".//mets:structMap[@LABEL='Normative Directory Structure']", namespaces=NSMAP
    )

    assert (
        normative_structmap[0]
        .xpath(
            f".//mets:div[@LABEL='{sip_directory_path.name}']",
            namespaces=NSMAP,
        )[0]
        .get("DMDID")
        == "dmdSec_2"
    )
    assert (
        normative_structmap[0]
        .xpath(".//mets:div[@LABEL='file1']", namespaces=NSMAP)[0]
        .get("DMDID")
        == "dmdSec_3"
    )
    assert (
        normative_structmap[0]
        .xpath(".//mets:div[@LABEL='empty_dir']", namespaces=NSMAP)[0]
        .get("DMDID")
        == "dmdSec_1"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "fail_on_error, errors, expectation",
    [
        (True, ["xml_validation_error"], pytest.raises(Exception)),
        (True, [], does_not_raise()),
        (False, ["xml_validation_error"], does_not_raise()),
        (False, [], does_not_raise()),
        (None, [], does_not_raise()),
    ],
)
@mock.patch(
    "archivematica.MCPClient.clientScripts.create_mets_v2.archivematicaCreateMETSMetadataXML.process_xml_metadata"
)
def test_xml_validation_fail_on_error(
    process_xml_metadata: mock.Mock,
    settings: pytest_django.fixtures.SettingsWrapper,
    mcp_job: Job,
    sip_directory_path: pathlib.Path,
    sip: SIP,
    sip_file: File,
    fail_on_error: bool,
    errors: list[str],
    expectation: does_not_raise,
) -> None:
    mock_mets = mock.Mock(
        **{
            "serialize.return_value": etree.Element("tag"),
            "get_subsections_counts.return_value": {},
        }
    )
    process_xml_metadata.return_value = (mock_mets, errors)
    if fail_on_error is not None:
        settings.XML_VALIDATION_FAIL_ON_ERROR = fail_on_error
    with expectation:
        main(
            mcp_job,
            sipType="SIP",
            baseDirectoryPath=str(sip_directory_path),
            XMLFile=str(sip_directory_path / "METS.xml"),
            sipUUID=sip.pk,
            includeAmdSec=False,
            createNormativeStructmap=False,
        )
    if errors:
        assert (
            "Error(s) processing and/or validating XML metadata:\n\t- xml_validation_error"
            in mcp_job.get_stderr()
        )


@pytest.fixture
def arranged_sip_path(tmp_path: pathlib.Path) -> pathlib.Path:
    sip_path = tmp_path / "sip"
    sip_path.mkdir()

    return sip_path


@pytest.fixture
def create_arrangement(sip: SIP, arranged_sip_path: pathlib.Path) -> None:
    # Create the directory structure representing the new arrangement.
    objects_path = arranged_sip_path / "objects"
    objects_path.mkdir()
    SIPArrange.objects.create(sip=sip, arrange_path=b".")

    for path, level_of_description in [
        ((arranged_sip_path / "objects" / "subdir"), "Series"),
        ((arranged_sip_path / "objects" / "subdir" / "first"), "Subseries"),
        ((arranged_sip_path / "objects" / "subdir" / "second"), "Subseries"),
    ]:
        path.mkdir()
        SIPArrange.objects.create(
            sip=sip,
            arrange_path=bytes(path.relative_to(objects_path)),
            level_of_description=level_of_description,
        )

    # Add files to the arrangement.
    for path in [
        (arranged_sip_path / "objects" / "file1"),
        (arranged_sip_path / "objects" / "subdir" / "file2"),
        (arranged_sip_path / "objects" / "subdir" / "first" / "file3"),
        (arranged_sip_path / "objects" / "subdir" / "second" / "file4"),
    ]:
        path.touch()
        f = File.objects.create(
            originallocation=f"%TransferDirectory%{path.relative_to(arranged_sip_path)}".encode(),
            currentlocation=f"%SIPDirectory%{path.relative_to(arranged_sip_path)}".encode(),
            sip=sip,
            filegrpuse="original",
        )
        SIPArrange.objects.create(
            sip=sip,
            arrange_path=bytes(path.relative_to(objects_path)),
            level_of_description="File",
            file_uuid=f.uuid,
        )


@pytest.mark.django_db
def test_structmap_is_created_from_sip_arrangement(
    mcp_job: Job, create_arrangement: None, arranged_sip_path: pathlib.Path, sip: SIP
) -> None:
    mets_path = f"{arranged_sip_path}/METS.{sip.uuid}.xml"

    main(
        mcp_job,
        sipType="SIP",
        baseDirectoryPath=(arranged_sip_path),
        XMLFile=mets_path,
        sipUUID=sip.pk,
        includeAmdSec=False,
        createNormativeStructmap=False,
    )

    # Verify the logical structMap for the SIP arrangement.
    mets_xml = etree.parse(mets_path)
    logical_structmap = mets_xml.find(
        './/mets:structMap[@TYPE="logical"]', namespaces=NSMAP
    )
    assert logical_structmap.attrib["LABEL"] == "Hierarchical"

    # Get the relevant elements from the logical structMap.
    sip_div = logical_structmap.find('mets:div[@LABEL="sip"]', namespaces=NSMAP)
    objects_div = sip_div.find('mets:div[@LABEL="objects"]', namespaces=NSMAP)
    file1_div = objects_div.find('mets:div[@LABEL="file1"]', namespaces=NSMAP)
    subdir_div = objects_div.find('mets:div[@LABEL="subdir"]', namespaces=NSMAP)
    file2_div = subdir_div.find('mets:div[@LABEL="file2"]', namespaces=NSMAP)
    subdir_first_div = subdir_div.find('mets:div[@LABEL="first"]', namespaces=NSMAP)
    file3_div = subdir_first_div.find('mets:div[@LABEL="file3"]', namespaces=NSMAP)
    subdir_second_div = subdir_div.find('mets:div[@LABEL="second"]', namespaces=NSMAP)
    file4_div = subdir_second_div.find('mets:div[@LABEL="file4"]', namespaces=NSMAP)

    # Verify the levels of descriptions are preserved.
    assert file1_div.attrib["TYPE"] == "File"
    assert subdir_div.attrib["TYPE"] == "Series"
    assert file2_div.attrib["TYPE"] == "File"
    assert subdir_first_div.attrib["TYPE"] == "Subseries"
    assert file3_div.attrib["TYPE"] == "File"
    assert subdir_second_div.attrib["TYPE"] == "Subseries"
    assert file4_div.attrib["TYPE"] == "File"


@pytest.fixture
def bag_path(sip_directory_path: pathlib.Path, sip: SIP) -> pathlib.Path:
    result = (
        sip_directory_path / "logs" / "transfers" / str(sip.uuid) / "logs" / "BagIt"
    )
    result.mkdir(parents=True)
    (result / "bag-info.txt").touch()

    return result


@pytest.mark.django_db
@mock.patch("archivematica.MCPClient.clientScripts.create_mets_v2.Bag")
@pytest.mark.parametrize(
    "info",
    [
        {"Bagging-Date": "2025-01-08", "Payload-Oxum": "0.2"},
        {},
    ],
    ids=[
        "populated",
        "empty",
    ],
)
def test_bag_metadata_is_recorded_in_a_amdsec(
    bag_class: mock.Mock,
    info: dict[str, str],
    mcp_job: Job,
    sip_directory_path: pathlib.Path,
    sip: SIP,
    sip_file: File,
    bag_path: pathlib.Path,
) -> None:
    bag_class.return_value = mock.Mock(info=info)
    mets_path = sip_directory_path / f"METS.{sip.uuid}.xml"

    main(
        mcp_job,
        sipType="SIP",
        baseDirectoryPath=str(sip_directory_path),
        XMLFile=str(mets_path),
        sipUUID=sip.pk,
        includeAmdSec=False,
        createNormativeStructmap=False,
    )

    mets_xml = etree.parse(mets_path.open())
    transfer_metadata = mets_xml.xpath(
        ".//mets:amdSec//transfer_metadata/*",
        namespaces=NSMAP,
    )
    assert {e.tag: e.text for e in transfer_metadata} == info


@pytest.fixture()
def transfer_metadata_xml_path(sip: SIP, sip_directory_path: pathlib.Path) -> File:
    metadata_dir_path = sip_directory_path / "objects" / "metadata" / "transfers"
    metadata_dir_path.mkdir(parents=True)

    result = metadata_dir_path / "transfer_metadata.xml"
    result.touch()

    return result


@pytest.fixture()
def transfer_metadata_xml(
    sip: SIP, sip_directory_path: pathlib.Path, transfer_metadata_xml_path: pathlib.Path
) -> File:
    return File.objects.create(
        sip=sip,
        currentlocation=f"%SIPDirectory%{transfer_metadata_xml_path.relative_to(sip_directory_path)}".encode(),
        filegrpuse="metadata",
    )


@pytest.mark.django_db
def test_transfer_metadata_xml_is_recorded_in_a_amdsec(
    mcp_job: Job,
    sip_directory_path: pathlib.Path,
    sip: SIP,
    sip_file: File,
    transfer_metadata_xml_path: pathlib.Path,
    transfer_metadata_xml: File,
) -> None:
    info = {"test": "foobar"}
    for tag, value in info.items():
        transfer_metadata_xml_path.write_text(f"<{tag}>{value}</{tag}>")
    mets_path = sip_directory_path / f"METS.{sip.uuid}.xml"

    main(
        mcp_job,
        sipType="SIP",
        baseDirectoryPath=str(sip_directory_path),
        XMLFile=str(mets_path),
        sipUUID=sip.pk,
        includeAmdSec=False,
        createNormativeStructmap=False,
    )

    mets_xml = etree.parse(mets_path.open())
    transfer_metadata = mets_xml.xpath(
        ".//mets:amdSec//mets:xmlData/*",
        namespaces=NSMAP,
    )
    assert {e.tag: e.text for e in transfer_metadata} == info


@pytest.fixture()
def source_metadata_xml_path(sip: SIP, sip_directory_path: pathlib.Path) -> File:
    metadata_dir_path = (
        sip_directory_path / "objects" / "metadata" / "transfers" / "sourceMD"
    )
    metadata_dir_path.mkdir(parents=True)

    result = metadata_dir_path / "file.xml"
    result.touch()

    return result


@pytest.fixture()
def source_metadata_xml(
    sip: SIP, sip_directory_path: pathlib.Path, source_metadata_xml_path: pathlib.Path
) -> File:
    return File.objects.create(
        sip=sip,
        currentlocation=f"%SIPDirectory%{source_metadata_xml_path.relative_to(sip_directory_path)}".encode(),
        filegrpuse="metadata",
    )


@pytest.mark.django_db
def test_source_metadata_xml_is_recorded_in_a_amdsec(
    mcp_job: Job,
    sip_directory_path: pathlib.Path,
    sip: SIP,
    sip_file: File,
    source_metadata_xml_path: pathlib.Path,
    source_metadata_xml: File,
) -> None:
    mets_path = sip_directory_path / f"METS.{sip.uuid}.xml"

    main(
        mcp_job,
        sipType="SIP",
        baseDirectoryPath=str(sip_directory_path),
        XMLFile=str(mets_path),
        sipUUID=sip.pk,
        includeAmdSec=False,
        createNormativeStructmap=False,
    )

    mets_xml = etree.parse(mets_path.open())
    transfer_metadata = mets_xml.xpath(
        ".//mets:amdSec//mets:mdRef",
        namespaces=NSMAP,
    )
    assert len(transfer_metadata) == 1
    assert transfer_metadata[0].attrib == {
        f"{{{NSMAP['xlink']}}}href": str(
            source_metadata_xml_path.relative_to(sip_directory_path)
        ),
        "MDTYPE": "OTHER",
        "LOCTYPE": "OTHER",
        "OTHERLOCTYPE": "SYSTEM",
    }
