from copy import deepcopy
import json
from pathlib import Path

from metadata_automation import generate
import pytest

from pinterest_automation.errors import PublishError
from pinterest_automation.media import read_text
from pinterest_automation.service import normalized, publish
from conftest import Adapter, fake_session


@pytest.fixture
def metadata(tmp_path):
    value = generate({"language": "en", "title": "Caribbean", "summary": "Island life.", "keywords": ["Caribbean"]}, schema_version="2.0.0")
    value["platforms"]["pinterest"] = {"title": "  Exact Pinterest title  ", "description": "  Exact\r\nPinterest description #One #Two \n", "hashtags": ["#One", "#Two"]}
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(value))
    return path, value


def test_full_v2_exact_text_and_equivalent_fingerprint(metadata, arguments):
    path, value = metadata
    title, description = read_text(metadata_file=str(path))
    assert title == value["platforms"]["pinterest"]["title"]
    assert description == value["platforms"]["pinterest"]["description"] and description.count("#One") == description.count("#Two") == 1
    Path(arguments.title_file).write_bytes(title.encode())
    Path(arguments.description_file).write_bytes(description.encode())
    manual = normalized(arguments)
    arguments.title_file = arguments.description_file = None
    arguments.metadata_file = str(path)
    assert normalized(arguments) == manual


@pytest.mark.parametrize("mutation", ["v1", "missing_other_platform", "missing_pinterest", "extra_field", "flat", "long_title", "long_description", "unknown_version"])
async def test_invalid_metadata_fails_before_browser(metadata, state, arguments, mutation):
    path, value = metadata
    if mutation == "v1":
        value = generate({"language": "en", "title": "Title", "summary": "Summary"})
    elif mutation == "missing_other_platform":
        del value["platforms"]["youtube"]
    elif mutation == "missing_pinterest":
        del value["platforms"]["pinterest"]
    elif mutation == "extra_field":
        value["platforms"]["pinterest"]["visibility"] = "public"
    elif mutation == "flat":
        value = value["platforms"]["pinterest"]
    elif mutation == "long_title":
        value["platforms"]["pinterest"]["title"] = "x" * 101
    elif mutation == "long_description":
        value["platforms"]["pinterest"]["description"] = "x" * 801
    else:
        value["schema_version"] = "999.0.0"
    path.write_text(json.dumps(value))
    arguments.title_file = arguments.description_file = None
    arguments.metadata_file = str(path)
    with pytest.raises(PublishError) as error:
        await publish(state, arguments, fake_session, Adapter)
    assert error.value.exit_code == 2 and Adapter.clicks == 0 and state.get("brand", arguments.request_id) is None


@pytest.mark.parametrize("manual", ["title", "description", "both"])
def test_mixed_sources_rejected(metadata, arguments, manual):
    arguments.metadata_file = str(metadata[0])
    title = arguments.title_file if manual in {"title", "both"} else None
    description = arguments.description_file if manual in {"description", "both"} else None
    with pytest.raises(PublishError) as error:
        read_text(title, description, arguments.metadata_file)
    assert error.value.code == "INVALID_ARGUMENTS"
