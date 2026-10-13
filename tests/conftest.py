import os
import sys

import pytest

from flask import Flask
from flask.globals import app_ctx as _app_ctx


@pytest.fixture(autouse=True)
def _standard_os_environ(monkeypatch):
    """在每次测试开始时设置 ``os.environ`` 以获得标准值。"""
    for key in (
        "FLASK_ENV_FILE",
        "FLASK_APP",
        "FLASK_DEBUG",
        "FLASK_RUN_FROM_CLI",
        "WERKZEUG_RUN_MAIN",
    ):
        monkeypatch.delenv(key, False)

    yield


@pytest.fixture
def app():
    app = Flask("flask_test", root_path=os.path.dirname(__file__))
    app.config.update(
        TESTING=True,
        SECRET_KEY="test key",
    )
    return app


@pytest.fixture
def app_ctx(app):
    with app.app_context() as ctx:
        yield ctx


@pytest.fixture
def req_ctx(app):
    with app.test_request_context() as ctx:
        yield ctx


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def test_apps(monkeypatch):
    monkeypatch.syspath_prepend(os.path.join(os.path.dirname(__file__), "test_apps"))
    original_modules = set(sys.modules.keys())

    yield

    # 删除测试期间缓存的所有导入。否则 "import app" 将在下一个测试中工作，即使它不再在路
    # 径上。
    for key in sys.modules.keys() - original_modules:
        sys.modules.pop(key)


@pytest.fixture(autouse=True)
def leak_detector():
    """如果测试结束时仍然推送任何应用程序上下文，则会失败。弹出所有上下文，以便后续测试
    不受影响。
    """
    yield
    leaks = []

    while _app_ctx:
        leaks.append(_app_ctx._get_current_object())
        _app_ctx.pop()

    assert not leaks


@pytest.fixture
def modules_tmp_path(tmp_path, monkeypatch):
    """添加到 sys.path 的临时目录。"""
    rv = tmp_path / "modules_tmp"
    rv.mkdir()
    monkeypatch.syspath_prepend(os.fspath(rv))
    return rv


@pytest.fixture
def modules_tmp_path_prefix(modules_tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", os.fspath(modules_tmp_path))
    return modules_tmp_path


@pytest.fixture
def site_packages(modules_tmp_path, monkeypatch):
    """创建一个假站点包。"""
    py_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
    rv = modules_tmp_path / "lib" / py_dir / "site-packages"
    rv.mkdir(parents=True)
    monkeypatch.syspath_prepend(os.fspath(rv))
    return rv


@pytest.fixture
def purge_module(request):
    def inner(name):
        request.addfinalizer(lambda: sys.modules.pop(name, None))

    return inner
