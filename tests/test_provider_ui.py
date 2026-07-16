from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_provider_page_has_masked_secret_manual_models_tests_and_activation(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("FASHION_CMS_DB_PATH", str(tmp_path / "providers.sqlite3"))
    app = AppTest.from_string(
        "from app import llm_providers_page\nllm_providers_page()\n",
        default_timeout=20,
    ).run()

    assert not app.exception
    assert "LLM Providers" in [element.value for element in app.title]
    inputs = {element.label: element for element in app.text_input}
    assert inputs["API Key"].proto.type == 1
    assert inputs["API Key"].value == ""
    assert "Vision Model" in inputs
    assert "Catalog/Text Model" in inputs
    assert {
        "Save",
        "Fetch Models / Refresh",
        "Test Connection",
        "Test Structured Output",
        "Test Vision",
        "Save and Activate",
    } <= {element.label for element in app.button}
    assert "Purposes to activate" in [element.label for element in app.multiselect]
