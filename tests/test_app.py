"""Streamlit 화면 스모크 테스트 (AppTest, 브라우저 없이 실행)."""
from streamlit.testing.v1 import AppTest


def _app():
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_diagnose_flow():
    at = _app()
    labels = [b.label for b in at.button]
    assert "최신값 불러오기" in labels and "진단 실행" in labels
    next(b for b in at.button if b.label == "최신값 불러오기").click().run()
    next(b for b in at.button if b.label == "진단 실행").click().run()
    assert not at.exception, at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "베어링 열화" in text and "긴급" in text


def test_required_input_error_shown():
    at = _app()
    next(b for b in at.button if b.label == "진단 실행").click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "토출압력 값을 입력하세요" in text


PAGE_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
import importlib
from ui.common import init_state, inject_css
init_state()
inject_css()
importlib.import_module({module!r}).page()
"""


def test_other_pages_render():
    import pathlib
    root = str(pathlib.Path(__file__).resolve().parent.parent)
    for module in ("ui.trend_page", "ui.priority_page"):
        at = AppTest.from_string(PAGE_SCRIPT.format(root=root, module=module), default_timeout=60)
        at.run()
        assert not at.exception, (module, at.exception)
        assert at.title, module
