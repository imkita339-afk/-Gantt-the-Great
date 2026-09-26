import copy
import json

import pytest

from gantt.exchange import OpenError, check
from gantt.formats import open_path

EXAMPLES = ["full", "incremental", "changes", "results", "other-system-flat"]


def load(root, name):
    return json.loads((root / "examples" / f"{name}.gantt.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_pass(root, name):
    assert check(load(root, name)).errors == []


def test_flat_file_of_other_system_opens(root):
    res = open_path(root / "examples" / "other-system-flat.gantt.json")
    assert len(res.document["nodes"]) == 6


def test_errors_are_russian_and_name_the_node(root):
    doc = copy.deepcopy(load(root, "other-system-flat"))
    doc["nodes"][2]["planEnd"] = "23.09.26"
    doc["nodes"][3]["status"] = "st-07"
    errors = check(doc).errors
    assert any("№ HD-103" in e and "План окончания" in e and "ГГГГ-ММ-ДД" in e for e in errors), errors
    assert any("st-07" in e and "statuses" in e for e in errors), errors


def test_unsupported_schema_version(root):
    doc = load(root, "other-system-flat")
    doc["schemaVersion"] = "2.0"
    assert any("не поддерживается" in e for e in check(doc).errors)


def test_json_with_bom_opens(root, tmp_path):
    src = (root / "examples" / "other-system-flat.gantt.json").read_bytes()
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + src)
    assert open_path(path).document["format"] == "gantt-exchange"


def test_changes_document_is_recognized_not_opened_as_data(root):
    assert open_path(root / "examples" / "changes.gantt.json").kind == "changes"


def test_missing_file(tmp_path):
    with pytest.raises(OpenError, match="не найден"):
        open_path(tmp_path / "нет такого файла.json")
