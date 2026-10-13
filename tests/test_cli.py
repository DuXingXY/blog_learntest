# 该文件是 Flask-CLI 的一部分，并根据其修订版 BSD 许可证的条款进行了修改。版权所有 ©
# 2015 CERN。
import importlib.metadata
import os
import platform
import ssl
import sys
import types
from functools import partial
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from flask import Blueprint
from flask import current_app
from flask import Flask
from flask.cli import AppGroup
from flask.cli import find_best_app
from flask.cli import FlaskGroup
from flask.cli import get_version
from flask.cli import load_dotenv
from flask.cli import locate_app
from flask.cli import NoAppException
from flask.cli import prepare_import
from flask.cli import run_command
from flask.cli import ScriptInfo
from flask.cli import with_appcontext

cwd = Path.cwd()
test_path = (Path(__file__) / ".." / "test_apps").resolve()


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_name(test_apps):
    """确保 CLI 对象的名称是应用程序的名称，而不是应用程序本身"""
    from cliapp.app import testapp

    assert testapp.cli.name == testapp.name


def test_find_best_app(test_apps):
    class Module:
        app = Flask("appname")

    assert find_best_app(Module) == Module.app

    class Module:
        application = Flask("appname")

    assert find_best_app(Module) == Module.application

    class Module:
        myapp = Flask("appname")

    assert find_best_app(Module) == Module.myapp

    class Module:
        @staticmethod
        def create_app():
            return Flask("appname")

    app = find_best_app(Module)
    assert isinstance(app, Flask)
    assert app.name == "appname"

    class Module:
        @staticmethod
        def create_app(**kwargs):
            return Flask("appname")

    app = find_best_app(Module)
    assert isinstance(app, Flask)
    assert app.name == "appname"

    class Module:
        @staticmethod
        def make_app():
            return Flask("appname")

    app = find_best_app(Module)
    assert isinstance(app, Flask)
    assert app.name == "appname"

    class Module:
        myapp = Flask("appname1")

        @staticmethod
        def create_app():
            return Flask("appname2")

    assert find_best_app(Module) == Module.myapp

    class Module:
        myapp = Flask("appname1")

        @staticmethod
        def create_app():
            return Flask("appname2")

    assert find_best_app(Module) == Module.myapp

    class Module:
        pass

    pytest.raises(NoAppException, find_best_app, Module)

    class Module:
        myapp1 = Flask("appname1")
        myapp2 = Flask("appname2")

    pytest.raises(NoAppException, find_best_app, Module)

    class Module:
        @staticmethod
        def create_app(foo, bar):
            return Flask("appname2")

    pytest.raises(NoAppException, find_best_app, Module)

    class Module:
        @staticmethod
        def create_app():
            raise TypeError("bad bad factory!")

    pytest.raises(TypeError, find_best_app, Module)


@pytest.mark.parametrize(
    "value,path,result",
    (
        ("test", cwd, "test"),
        ("test.py", cwd, "test"),
        ("a/test", cwd / "a", "test"),
        ("test/__init__.py", cwd, "test"),
        ("test/__init__", cwd, "test"),
        # 嵌套包
        (
            test_path / "cliapp" / "inner1" / "__init__",
            test_path,
            "cliapp.inner1",
        ),
        (
            test_path / "cliapp" / "inner1" / "inner2",
            test_path,
            "cliapp.inner1.inner2",
        ),
        # 点名称
        ("test.a.b", cwd, "test.a.b"),
        (test_path / "cliapp.app", test_path, "cliapp.app"),
        # 不是 Python 文件，将在导入过程中被捕获
        (test_path / "cliapp" / "message.txt", test_path, "cliapp.message.txt"),
    ),
)
def test_prepare_import(request, value, path, result):
    """期望设置正确的路径并返回正确的导入和应用程序名称。

    :func:`prepare_exec_for_file` 有一个副作用，即给定导入的父目录被添加到
    :data:`sys.path` 。测试运行后此值会重置。
    """
    original_path = sys.path[:]

    def reset_path():
        sys.path[:] = original_path

    request.addfinalizer(reset_path)

    assert prepare_import(value) == result
    assert sys.path[0] == str(path)


@pytest.mark.parametrize(
    "iname,aname,result",
    (
        ("cliapp.app", None, "testapp"),
        ("cliapp.app", "testapp", "testapp"),
        ("cliapp.factory", None, "app"),
        ("cliapp.factory", "create_app", "app"),
        ("cliapp.factory", "create_app()", "app"),
        ("cliapp.factory", 'create_app2("foo", "bar")', "app2_foo_bar"),
        # 尾随逗号空格
        ("cliapp.factory", 'create_app2("foo", "bar", )', "app2_foo_bar"),
        # 去除空白
        ("cliapp.factory", " create_app () ", "app"),
    ),
)
def test_locate_app(test_apps, iname, aname, result):
    assert locate_app(iname, aname).name == result


@pytest.mark.parametrize(
    "iname,aname",
    (
        ("notanapp.py", None),
        ("cliapp/app", None),
        ("cliapp.app", "notanapp"),
        # 没有足够的论据
        ("cliapp.factory", 'create_app2("foo")'),
        # 无效的标识符
        ("cliapp.factory", "create_app("),
        # 没有返回应用程序
        ("cliapp.factory", "no_app"),
        # 嵌套导入错误
        ("cliapp.importerrorapp", None),
        # 不是 Python 文件
        ("cliapp.message.txt", None),
    ),
)
def test_locate_app_raises(test_apps, iname, aname):
    with pytest.raises(NoAppException):
        locate_app(iname, aname)


def test_locate_app_suppress_raise(test_apps):
    app = locate_app("notanapp.py", None, raise_if_not_found=False)
    assert app is None

    # 仅抑制直接导入错误
    with pytest.raises(NoAppException):
        locate_app("cliapp.importerrorapp", None, raise_if_not_found=False)


def test_get_version(test_apps, capsys):
    class MockCtx:
        resilient_parsing = False
        color = None

        def exit(self):
            return

    ctx = MockCtx()
    get_version(ctx, None, "test")
    out, err = capsys.readouterr()
    assert f"Python {platform.python_version()}" in out
    assert f"Flask {importlib.metadata.version('flask')}" in out
    assert f"Werkzeug {importlib.metadata.version('werkzeug')}" in out


def test_scriptinfo(test_apps, monkeypatch):
    obj = ScriptInfo(app_import_path="cliapp.app:testapp")
    app = obj.load_app()
    assert app.name == "testapp"
    assert obj.load_app() is app

    # 使用模块的绝对路径导入应用程序
    cli_app_path = str(test_path / "cliapp" / "app.py")

    obj = ScriptInfo(app_import_path=cli_app_path)
    app = obj.load_app()
    assert app.name == "testapp"
    assert obj.load_app() is app
    obj = ScriptInfo(app_import_path=f"{cli_app_path}:testapp")
    app = obj.load_app()
    assert app.name == "testapp"
    assert obj.load_app() is app

    def create_app():
        return Flask("createapp")

    obj = ScriptInfo(create_app=create_app)
    app = obj.load_app()
    assert app.name == "createapp"
    assert obj.load_app() is app

    obj = ScriptInfo()
    pytest.raises(NoAppException, obj.load_app)

    # 从当前目录中的 wsgi.py 导入应用程序
    monkeypatch.chdir(test_path / "helloworld")
    obj = ScriptInfo()
    app = obj.load_app()
    assert app.name == "hello"

    # 从当前目录中的 app.py 导入应用程序
    monkeypatch.chdir(test_path / "cliapp")
    obj = ScriptInfo()
    app = obj.load_app()
    assert app.name == "testapp"


def test_app_cli_has_app_context(app, runner):
    def _param_cb(ctx, param, value):
        # current_app 应该在参数回调中可用
        return bool(current_app)

    @app.cli.command()
    @click.argument("value", callback=_param_cb)
    def check(value):
        app = click.get_current_context().obj.load_app()
        # 加载的应用程序应与 current_app 相同
        same_app = current_app._get_current_object() is app
        return same_app, value

    cli = FlaskGroup(create_app=lambda: app)
    result = runner.invoke(cli, ["check", "x"], standalone_mode=False)
    assert result.return_value == (True, True)


def test_with_appcontext(runner):
    @click.command()
    @with_appcontext
    def testcmd():
        click.echo(current_app.name)

    obj = ScriptInfo(create_app=lambda: Flask("testapp"))

    result = runner.invoke(testcmd, obj=obj)
    assert result.exit_code == 0
    assert result.output == "testapp\n"


def test_appgroup_app_context(runner):
    @click.group(cls=AppGroup)
    def cli():
        pass

    @cli.command()
    def test():
        click.echo(current_app.name)

    @cli.group()
    def subgroup():
        pass

    @subgroup.command()
    def test2():
        click.echo(current_app.name)

    obj = ScriptInfo(create_app=lambda: Flask("testappgroup"))

    result = runner.invoke(cli, ["test"], obj=obj)
    assert result.exit_code == 0
    assert result.output == "testappgroup\n"

    result = runner.invoke(cli, ["subgroup", "test2"], obj=obj)
    assert result.exit_code == 0
    assert result.output == "testappgroup\n"


def test_flaskgroup_app_context(runner):
    def create_app():
        return Flask("flaskgroup")

    @click.group(cls=FlaskGroup, create_app=create_app)
    def cli(**params):
        pass

    @cli.command()
    def test():
        click.echo(current_app.name)

    result = runner.invoke(cli, ["test"])
    assert result.exit_code == 0
    assert result.output == "flaskgroup\n"


@pytest.mark.parametrize("set_debug_flag", (True, False))
def test_flaskgroup_debug(runner, set_debug_flag):
    def create_app():
        app = Flask("flaskgroup")
        app.debug = True
        return app

    @click.group(cls=FlaskGroup, create_app=create_app, set_debug_flag=set_debug_flag)
    def cli(**params):
        pass

    @cli.command()
    def test():
        click.echo(str(current_app.debug))

    result = runner.invoke(cli, ["test"])
    assert result.exit_code == 0
    assert result.output == f"{not set_debug_flag}\n"


def test_flaskgroup_nested(app, runner):
    cli = click.Group("cli")
    flask_group = FlaskGroup(name="flask", create_app=lambda: app)
    cli.add_command(flask_group)

    @flask_group.command()
    def show():
        click.echo(current_app.name)

    result = runner.invoke(cli, ["flask", "show"])
    assert result.output == "flask_test\n"


def test_no_command_echo_loading_error():
    from flask.cli import cli

    try:
        runner = CliRunner(mix_stderr=False)
    except (DeprecationWarning, TypeError):
        # Click >= 8.2
        runner = CliRunner()

    result = runner.invoke(cli, ["missing"])
    assert result.exit_code == 2
    assert "FLASK_APP" in result.stderr
    assert "Usage:" in result.stderr


def test_help_echo_loading_error():
    from flask.cli import cli

    try:
        runner = CliRunner(mix_stderr=False)
    except (DeprecationWarning, TypeError):
        # Click >= 8.2
        runner = CliRunner()

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "FLASK_APP" in result.stderr
    assert "Usage:" in result.stdout


def test_help_echo_exception():
    def create_app():
        raise Exception("oh no")

    cli = FlaskGroup(create_app=create_app)

    try:
        runner = CliRunner(mix_stderr=False)
    except (DeprecationWarning, TypeError):
        # Click >= 8.2
        runner = CliRunner()

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Exception: oh no" in result.stderr
    assert "Usage:" in result.stdout


class TestRoutes:
    @pytest.fixture
    def app(self):
        app = Flask(__name__)
        app.add_url_rule(
            "/get_post/<int:x>/<int:y>",
            methods=["GET", "POST"],
            endpoint="yyy_get_post",
        )
        app.add_url_rule("/zzz_post", methods=["POST"], endpoint="aaa_post")
        return app

    @pytest.fixture
    def invoke(self, app, runner):
        cli = FlaskGroup(create_app=lambda: app)
        return partial(runner.invoke, cli)

    def expect_order(self, order, output):
        # 跳过标题并匹配每行的开头
        for expect, line in zip(order, output.splitlines()[2:], strict=False):
            # 这样做而不是startswith以获得更好的pytest输出
            assert line[: len(expect)] == expect

    def test_simple(self, invoke):
        result = invoke(["routes"])
        assert result.exit_code == 0
        self.expect_order(["aaa_post", "static", "yyy_get_post"], result.output)

    def test_sort(self, app, invoke):
        default_output = invoke(["routes"]).output
        endpoint_output = invoke(["routes", "-s", "endpoint"]).output
        assert default_output == endpoint_output
        self.expect_order(
            ["static", "yyy_get_post", "aaa_post"],
            invoke(["routes", "-s", "methods"]).output,
        )
        self.expect_order(
            ["yyy_get_post", "static", "aaa_post"],
            invoke(["routes", "-s", "rule"]).output,
        )
        match_order = [r.endpoint for r in app.url_map.iter_rules()]
        self.expect_order(match_order, invoke(["routes", "-s", "match"]).output)

    def test_all_methods(self, invoke):
        output = invoke(["routes"]).output
        assert "GET, HEAD, OPTIONS, POST" not in output

        output = invoke(["routes", "--all-methods"]).output
        assert "GET, HEAD, OPTIONS, POST" in output

    def test_no_routes(self, runner):
        app = Flask(__name__, static_folder=None)
        cli = FlaskGroup(create_app=lambda: app)
        result = runner.invoke(cli, ["routes"])
        assert result.exit_code == 0
        assert "No routes were registered." in result.output

    def test_subdomain(self, runner):
        app = Flask(__name__, static_folder=None)
        app.add_url_rule("/a", subdomain="a", endpoint="a")
        app.add_url_rule("/b", subdomain="b", endpoint="b")
        cli = FlaskGroup(create_app=lambda: app)
        result = runner.invoke(cli, ["routes"])
        assert result.exit_code == 0
        assert "Subdomain" in result.output

    def test_host(self, runner):
        app = Flask(__name__, static_folder=None, host_matching=True)
        app.add_url_rule("/a", host="a", endpoint="a")
        app.add_url_rule("/b", host="b", endpoint="b")
        cli = FlaskGroup(create_app=lambda: app)
        result = runner.invoke(cli, ["routes"])
        assert result.exit_code == 0
        assert "Host" in result.output


def dotenv_not_available():
    try:
        import dotenv  # noqa: F401
    except ImportError:
        return True

    return False


need_dotenv = pytest.mark.skipif(
    dotenv_not_available(), reason="dotenv is not installed"
)


@need_dotenv
def test_load_dotenv(monkeypatch):
    for item in ("FOO", "BAR", "SPAM", "HAM"):
        monkeypatch.delenv(item, False)

    monkeypatch.setenv("EGGS", "3")
    monkeypatch.chdir(test_path)
    assert load_dotenv()
    assert Path.cwd() == test_path
    # .flaskenv 不会覆盖 .env
    assert os.environ["FOO"] == "env"
    # 仅在 .flaskenv 中设置
    assert os.environ["BAR"] == "bar"
    # 仅在 .env 中设置
    assert os.environ["SPAM"] == "1"
    # 手动设置，文件不会覆盖
    assert os.environ["EGGS"] == "3"
    # 测试 env 文件编码
    assert os.environ["HAM"] == "火腿"
    # 不存在的文件不应加载
    assert not load_dotenv("non-existent-file", load_defaults=False)


@need_dotenv
def test_dotenv_path(monkeypatch):
    for item in ("FOO", "BAR", "EGGS"):
        monkeypatch.delenv(item, False)

    load_dotenv(test_path / ".flaskenv")
    assert Path.cwd() == cwd
    assert "FOO" in os.environ


def test_dotenv_optional(monkeypatch):
    monkeypatch.delenv("FOO", False)
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.chdir(test_path)
    load_dotenv()
    assert "FOO" not in os.environ


@need_dotenv
def test_disable_dotenv_from_env(monkeypatch, runner):
    monkeypatch.delenv("FOO", False)
    monkeypatch.chdir(test_path)
    monkeypatch.setitem(os.environ, "FLASK_SKIP_DOTENV", "1")
    runner.invoke(FlaskGroup())
    assert "FOO" not in os.environ


def test_run_cert_path():
    # 没有钥匙
    with pytest.raises(click.BadParameter):
        run_command.make_context("run", ["--cert", __file__])

    # 没有证书
    with pytest.raises(click.BadParameter):
        run_command.make_context("run", ["--key", __file__])

    # 首先指定证书
    ctx = run_command.make_context("run", ["--cert", __file__, "--key", __file__])
    assert ctx.params["cert"] == (__file__, __file__)

    # 首先指定的键
    ctx = run_command.make_context("run", ["--key", __file__, "--cert", __file__])
    assert ctx.params["cert"] == (__file__, __file__)


def test_run_cert_adhoc(monkeypatch):
    monkeypatch.setitem(sys.modules, "cryptography", None)

    # 未安装加密技术
    with pytest.raises(click.BadParameter):
        run_command.make_context("run", ["--cert", "adhoc"])

    # 安装了加密技术
    monkeypatch.setitem(sys.modules, "cryptography", types.ModuleType("cryptography"))
    ctx = run_command.make_context("run", ["--cert", "adhoc"])
    assert ctx.params["cert"] == "adhoc"

    # 没有特别的密钥
    with pytest.raises(click.BadParameter):
        run_command.make_context("run", ["--cert", "adhoc", "--key", __file__])


def test_run_cert_import(monkeypatch):
    monkeypatch.setitem(sys.modules, "not_here", None)

    # 导入错误
    with pytest.raises(click.BadParameter):
        run_command.make_context("run", ["--cert", "not_here"])

    with pytest.raises(click.BadParameter):
        run_command.make_context("run", ["--cert", "flask"])

    # SSL上下文
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

    monkeypatch.setitem(sys.modules, "ssl_context", ssl_context)
    ctx = run_command.make_context("run", ["--cert", "ssl_context"])
    assert ctx.params["cert"] is ssl_context

    # SSLContext 没有 --key
    with pytest.raises(click.BadParameter):
        run_command.make_context("run", ["--cert", "ssl_context", "--key", __file__])


def test_run_cert_no_ssl(monkeypatch):
    monkeypatch.setitem(sys.modules, "ssl", None)

    with pytest.raises(click.BadParameter):
        run_command.make_context("run", ["--cert", "not_here"])


def test_cli_blueprints(app):
    """测试蓝图命令正确注册到应用程序"""
    custom = Blueprint("custom", __name__, cli_group="customized")
    nested = Blueprint("nested", __name__)
    merged = Blueprint("merged", __name__, cli_group=None)
    late = Blueprint("late", __name__)

    @custom.cli.command("custom")
    def custom_command():
        click.echo("custom_result")

    @nested.cli.command("nested")
    def nested_command():
        click.echo("nested_result")

    @merged.cli.command("merged")
    def merged_command():
        click.echo("merged_result")

    @late.cli.command("late")
    def late_command():
        click.echo("late_result")

    app.register_blueprint(custom)
    app.register_blueprint(nested)
    app.register_blueprint(merged)
    app.register_blueprint(late, cli_group="late_registration")

    app_runner = app.test_cli_runner()

    result = app_runner.invoke(args=["customized", "custom"])
    assert "custom_result" in result.output

    result = app_runner.invoke(args=["nested", "nested"])
    assert "nested_result" in result.output

    result = app_runner.invoke(args=["merged"])
    assert "merged_result" in result.output

    result = app_runner.invoke(args=["late_registration", "late"])
    assert "late_result" in result.output


def test_cli_empty(app):
    """如果 Blueprint 的 CLI 组为空，则不要注册它。"""
    bp = Blueprint("blue", __name__, cli_group="blue")
    app.register_blueprint(bp)

    result = app.test_cli_runner().invoke(args=["blue", "--help"])
    assert result.exit_code == 2, f"Unexpected success:\n\n{result.output}"


def test_run_exclude_patterns():
    ctx = run_command.make_context("run", ["--exclude-patterns", __file__])
    assert ctx.params["exclude_patterns"] == [__file__]
