"""pytest-фикстуры для namespace-тестов content-bot.

Многие тест-файлы (напр. test_subtitle_review_p4.py) — dual-mode: их можно гонять
как скрипт (`python tests/test_x.py` → `main()` сам передаёт список `errors`) ИЛИ под
pytest. В pytest-режиме функции `def test_*(errors)` ждут `errors` как ФИКСТУРУ — её
раньше не было нигде, поэтому все такие тесты падали с «fixture 'errors' not found»
(task_80d4b43c, ~54 файла). Эта фикстура инжектит список и в teardown ПАДАЕТ, если в
нём накопились FAIL'ы, — так тест корректно проходит/падает и под pytest.
"""
import pytest


@pytest.fixture
def errors():
    errs: list = []
    yield errs
    assert not errs, "накоплены провалы:\n" + "\n".join(errs)
