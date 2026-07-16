from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path

from PIL import Image
from openpyxl import Workbook
import pytest
from streamlit.testing.v1 import AppTest

from fashion_cms.database import JobDatabase
from fashion_cms.jobs import JobService
from fashion_cms.llm_service import FakeLLMClient
from fashion_cms.models import InputRow, JobStatus, UploadedImage, WorkItemStatus
from fashion_cms.registry import load_registry
from fashion_cms.topwear_extraction import run_topwear_job


def matched_image() -> UploadedImage:
    output = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, format="PNG")
    return UploadedImage(
        source_name="SKU-1-1.png",
        filename="SKU-1-1.png",
        sku="SKU-1",
        ordinal=1,
        image_format="PNG",
        width=8,
        height=8,
        content=output.getvalue(),
    )


def input_workbook_bytes() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        (
            "sku",
            "base_code",
            "attributes__lulu_ean",
            "attributes__shipping_weight",
            "input_data",
        )
    )
    worksheet.append(
        ("SKU-1", "BASE-1", "000123", "0.25", '{"color":"Blue"}')
    )
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_nvidia_connection_test_is_fixed_and_disabled_without_server_key(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("FASHION_CMS_DB_PATH", str(tmp_path / "nvidia-ui.sqlite3"))
    app = AppTest.from_file("app.py", default_timeout=20).run()

    assert not app.exception
    buttons = {element.label: element for element in app.button}
    assert buttons["Test NVIDIA Connection"].disabled
    assert not app.text_input
    assert not app.multiselect
    assert "Configure NVIDIA_API_KEY to enable live extraction." in [
        element.value for element in app.info
    ]


def test_connection_button_key_rotation_and_one_click_extraction(
    monkeypatch,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "input.xlsx"
    image_path = tmp_path / "SKU-1-1.png"
    request_log = tmp_path / "requests.jsonl"
    database_path = tmp_path / "ui.sqlite3"
    workbook_path.write_bytes(input_workbook_bytes())
    image_path.write_bytes(matched_image().content)
    monkeypatch.setenv("FASHION_CMS_DB_PATH", str(database_path))
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-first-test-key")
    app_path = str(Path(__file__).parents[1] / "app.py")
    script = f'''
import json
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from fashion_cms.llm_service import LLMResponse, NVIDIA_MODEL
from fashion_cms.topwear_extraction import fake_attribute_response

WORKBOOK = Path({str(workbook_path)!r})
IMAGE = Path({str(image_path)!r})
REQUEST_LOG = Path({str(request_log)!r})
APP = Path({app_path!r})

class Upload:
    def __init__(self, path):
        self.path = path
        self.name = path.name

    def getvalue(self):
        return self.path.read_bytes()

class FakeNvidia:
    def __init__(self, _settings):
        pass

    def close(self):
        pass

    def create(self, request):
        if request.work_item_key == "nvidia-connection-diagnostic":
            return LLMResponse(
                request_id="connection-test",
                model=NVIDIA_MODEL,
                status="completed",
                output_text='{{"shape":"square","color":"blue"}}',
            )
        with REQUEST_LOG.open("a", encoding="utf-8") as output:
            output.write(json.dumps(request.payload) + "\\n")
        return fake_attribute_response(request)

with patch.object(
    st,
    "file_uploader",
    side_effect=[Upload(WORKBOOK), [Upload(IMAGE)]],
), patch("fashion_cms.llm_service.NvidiaInklingClient", FakeNvidia):
    namespace = {{"__file__": str(APP), "__name__": "__main__"}}
    exec(compile(APP.read_text(encoding="utf-8"), str(APP), "exec"), namespace)
'''

    dashboard = AppTest.from_string(script, default_timeout=20).run()
    assert not dashboard.exception
    buttons = {button.label: button for button in dashboard.button}
    assert not buttons["Test NVIDIA Connection"].disabled
    assert buttons["Run Data Extraction"].disabled

    buttons["Test NVIDIA Connection"].click().run()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-rotated-test-key")
    dashboard.run()
    assert next(
        button for button in dashboard.button if button.label == "Run Data Extraction"
    ).disabled

    next(
        button for button in dashboard.button if button.label == "Test NVIDIA Connection"
    ).click().run()
    next(
        checkbox
        for checkbox in dashboard.checkbox
        if checkbox.label == "I confirm these provider calls may incur charges."
    ).check().run()
    run_button = next(
        button for button in dashboard.button if button.label == "Run Data Extraction"
    )
    assert not run_button.disabled
    run_button.click().run(timeout=20)

    database = JobDatabase(database_path)
    jobs = database.list_jobs()
    assert len(jobs) == 1
    job_id = jobs[0].id
    assert database.load_rows(job_id)[0].input_data == '{"color":"Blue"}'
    assert database.list_work_items(job_id)[0].status == WorkItemStatus.REVIEW_REQUIRED
    database.close()

    requests = [json.loads(line) for line in request_log.read_text().splitlines()]
    assert len(requests) == 1
    content = requests[0]["input"][1]["content"]
    prompt = content[0]["text"]
    assert "<INPUT_DATA_UNTRUSTED_JSON>" in prompt
    assert '{\\"color\\":\\"Blue\\"}' in prompt
    assert content[1]["text"] == "SKU: SKU-1 | IMAGE_ID: SKU-1-1"
    assert content[2]["image_url"].startswith("data:image/png;base64,")


def test_legacy_extraction_history_is_read_only_and_unfinished_work_cannot_resume(
    monkeypatch,
    tmp_path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    monkeypatch.setenv("FASHION_CMS_DB_PATH", str(database_path))
    registry = load_registry(Path(__file__).parents[1] / "config" / "attribute_registry.xlsx")
    database = JobDatabase(database_path)
    service = JobService(database)
    row = InputRow(row_number=2, sku="SKU-1", base_code="BASE", input_data="legacy evidence")
    image = matched_image()
    options = {
        "attribute_set": "topwear",
        "product_profile": "topwear_mvp",
        "registry_version": registry.fingerprint,
        "prompt_version": "topwear-extraction-v1",
        "schema_version": "topwear-structured-output-v1",
        "model_identifier": "historical-model",
        "image_detail": "high",
    }
    completed_id = service.create_job((row,), (image,), **options)
    assert service.run_job(completed_id).status == JobStatus.COMPLETED
    database.add_artifact(completed_id, "CMS_WORKBOOK", "legacy-output.xlsx")
    unfinished_id = service.create_job((row,), (image,), **options)

    completed_item = database.list_work_items(completed_id)[0]
    assert database.load_rows(completed_id)[0].input_data == "legacy evidence"
    assert database.get_work_item_result(completed_item) is not None
    assert database.list_artifacts(completed_id)[0].path == "legacy-output.xlsx"
    client = FakeLLMClient()
    with pytest.raises(ValueError, match="current extraction contract"):
        run_topwear_job(database, unfinished_id, client, (image,), registry)
    assert client.calls == []
    database.close()

    app_path = Path(__file__).parents[1] / "app.py"
    history = AppTest.from_string(
        f'''
from pathlib import Path

APP = Path({str(app_path)!r})
namespace = {{"__file__": str(APP), "__name__": "legacy_history_test"}}
source = APP.read_text(encoding="utf-8").rsplit("\\nst.set_page_config(", 1)[0]
exec(compile(source, str(APP), "exec"), namespace)
namespace["job_history_page"]()
''',
        default_timeout=20,
    ).run()
    assert not history.exception
    assert {
        "This pre-input_data extraction is read-only. Completed results and artifacts "
        "remain available; unfinished work requires a new upload."
    } <= {element.value for element in history.info}
    selector = next(
        selectbox for selectbox in history.selectbox if selectbox.label == "Open job details"
    )
    assert selector.value == unfinished_id
    assert all(any(job_id in option for option in selector.options) for job_id in (completed_id, unfinished_id))
    assert not {
        "Resume interrupted job",
        "Retry failed items",
    } & {button.label for button in history.button}
    selector.select(completed_id).run()
    assert not history.exception
    assert any("legacy-output.xlsx" in element.value for element in history.markdown)
