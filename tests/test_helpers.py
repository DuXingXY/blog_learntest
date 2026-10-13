import io
import os

import pytest
import werkzeug.exceptions

import flask
from flask.helpers import get_debug_flag


class FakePath:
    """代表 ``PathLike object`` 的假对象。

    这表示 python 3 中的 ``pathlib.Path`` 对象。请参阅：
    https://www.python.org/dev/peps/pep-0519/
    """

    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return self.path


class PyBytesIO:
    def __init__(self, *args, **kwargs):
        self._io = io.BytesIO(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._io, name)


class TestSendfile:
    def test_send_file(self, app, req_ctx):
        with app.open_resource("static/index.html") as f:
            expect = f.read()

        with flask.send_file("static/index.html") as rv:
            assert rv.direct_passthrough
            assert rv.mimetype == "text/html"
            rv.direct_passthrough = False
            assert rv.data == expect

    def test_static_file(self, app, req_ctx):
        # 默认 max_age 是 None。

        # 使用静态文件处理程序进行测试。
        with app.send_static_file("index.html") as rv:
            assert rv.cache_control.max_age is None

        # 直接使用 send_file 进行测试。
        with flask.send_file("static/index.html") as rv:
            assert rv.cache_control.max_age is None

        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

        # 使用静态文件处理程序进行测试。
        with app.send_static_file("index.html") as rv:
            assert rv.cache_control.max_age == 3600

        # 直接使用 send_file 进行测试。
        with flask.send_file("static/index.html") as rv:
            assert rv.cache_control.max_age == 3600

        # 使用 pathlib.Path 进行测试。
        with app.send_static_file(FakePath("index.html")) as rv:
            assert rv.cache_control.max_age == 3600

        class StaticFileApp(flask.Flask):
            def get_send_file_max_age(self, filename):
                return 10

        app = StaticFileApp(__name__)

        with app.test_request_context():
            # 使用静态文件处理程序进行测试。
            with app.send_static_file("index.html") as rv:
                assert rv.cache_control.max_age == 10

            # 直接使用 send_file 进行测试。
            with flask.send_file("static/index.html") as rv:
                assert rv.cache_control.max_age == 10

    def test_send_from_directory(self, app, req_ctx):
        app.root_path = os.path.join(
            os.path.dirname(__file__), "test_apps", "subdomaintestmodule"
        )

        with flask.send_from_directory("static", "hello.txt") as rv:
            rv.direct_passthrough = False
            assert rv.data.strip() == b"Hello Subdomain"


class TestUrlFor:
    def test_url_for_with_anchor(self, app, req_ctx):
        @app.route("/")
        def index():
            return "42"

        assert flask.url_for("index", _anchor="x y") == "/#x%20y"

    def test_url_for_with_scheme(self, app, req_ctx):
        @app.route("/")
        def index():
            return "42"

        assert (
            flask.url_for("index", _external=True, _scheme="https")
            == "https://localhost/"
        )

    def test_url_for_with_scheme_not_external(self, app, req_ctx):
        app.add_url_rule("/", endpoint="index")

        # 隐式外部与方案。
        url = flask.url_for("index", _scheme="https")
        assert url == "https://localhost/"

        # 当 external=False 使用方案时出错
        with pytest.raises(ValueError):
            flask.url_for("index", _scheme="https", _external=False)

    def test_url_for_with_alternating_schemes(self, app, req_ctx):
        @app.route("/")
        def index():
            return "42"

        assert flask.url_for("index", _external=True) == "http://localhost/"
        assert (
            flask.url_for("index", _external=True, _scheme="https")
            == "https://localhost/"
        )
        assert flask.url_for("index", _external=True) == "http://localhost/"

    def test_url_with_method(self, app, req_ctx):
        from flask.views import MethodView

        class MyView(MethodView):
            def get(self, id=None):
                if id is None:
                    return "List"
                return f"Get {id:d}"

            def post(self):
                return "Create"

        myview = MyView.as_view("myview")
        app.add_url_rule("/myview/", methods=["GET"], view_func=myview)
        app.add_url_rule("/myview/<int:id>", methods=["GET"], view_func=myview)
        app.add_url_rule("/myview/create", methods=["POST"], view_func=myview)

        assert flask.url_for("myview", _method="GET") == "/myview/"
        assert flask.url_for("myview", id=42, _method="GET") == "/myview/42"
        assert flask.url_for("myview", _method="POST") == "/myview/create"

    def test_url_for_with_self(self, app, req_ctx):
        @app.route("/<self>")
        def index(self):
            return "42"

        assert flask.url_for("index", self="2") == "/2"


def test_redirect_no_app():
    response = flask.redirect("https://localhost", 307)
    assert response.location == "https://localhost"
    assert response.status_code == 307


def test_redirect_with_app(app):
    def redirect(location, code=302):
        raise ValueError

    app.redirect = redirect

    with app.app_context(), pytest.raises(ValueError):
        flask.redirect("other")


def test_abort_no_app():
    with pytest.raises(werkzeug.exceptions.Unauthorized):
        flask.abort(401)

    with pytest.raises(LookupError):
        flask.abort(900)


def test_app_aborter_class():
    class MyAborter(werkzeug.exceptions.Aborter):
        pass

    class MyFlask(flask.Flask):
        aborter_class = MyAborter

    app = MyFlask(__name__)
    assert isinstance(app.aborter, MyAborter)


def test_abort_with_app(app):
    class My900Error(werkzeug.exceptions.HTTPException):
        code = 900

    app.aborter.mapping[900] = My900Error

    with app.app_context(), pytest.raises(My900Error):
        flask.abort(900)


class TestNoImports:
    """测试在不执行导入的情况下创建 Flask 实例。

    避免 ``__import__`` 有助于在导入时出现错误的情况下创建 Flask 实例。  这些运行时错误
    很快就会对用户显而易见，但是以元编程方式构建 Flask 实例的工具可以从 Flask 中受益，而
    Flask 则不会 ``__import__`` 。  不要通过导入来检索模块或包上的文件路径或元数据，而是
    使用 Python 标准库中的 pkgutil 和 imp 模块。
    """

    def test_name_with_import_error(self, modules_tmp_path):
        (modules_tmp_path / "importerror.py").write_text("raise NotImplementedError()")
        try:
            flask.Flask("importerror")
        except NotImplementedError:
            AssertionError("Flask(import_name) is importing import_name.")


class TestStreaming:
    def test_streaming_with_context(self, app, client):
        @app.route("/")
        def index():
            def generate():
                yield "Hello "
                yield flask.request.args["name"]
                yield "!"

            return flask.Response(flask.stream_with_context(generate()))

        rv = client.get("/?name=World")
        assert rv.data == b"Hello World!"

    def test_streaming_with_context_as_decorator(self, app, client):
        @app.route("/")
        def index():
            @flask.stream_with_context
            def generate(hello):
                yield hello
                yield flask.request.args["name"]
                yield "!"

            return flask.Response(generate("Hello "))

        rv = client.get("/?name=World")
        assert rv.data == b"Hello World!"

    def test_streaming_with_context_and_custom_close(self, app, client):
        called = []

        class Wrapper:
            def __init__(self, gen):
                self._gen = gen

            def __iter__(self):
                return self

            def close(self):
                called.append(42)

            def __next__(self):
                return next(self._gen)

            next = __next__

        @app.route("/")
        def index():
            def generate():
                yield "Hello "
                yield flask.request.args["name"]
                yield "!"

            return flask.Response(flask.stream_with_context(Wrapper(generate())))

        rv = client.get("/?name=World")
        assert rv.data == b"Hello World!"
        assert called == [42]

    def test_stream_keeps_session(self, app, client):
        @app.route("/")
        def index():
            flask.session["test"] = "flask"

            @flask.stream_with_context
            def gen():
                yield flask.session["test"]

            return flask.Response(gen())

        rv = client.get("/")
        assert rv.data == b"flask"

    def test_async_view(self, app, client):
        @app.route("/")
        async def index():
            flask.session["test"] = "flask"

            @flask.stream_with_context
            def gen():
                yield flask.session["test"]

            return flask.Response(gen())

        # 响应在未读取流的情况下关闭
        client.get().close()

        # 响应流被读取
        with client.get() as rv:
            assert rv.text == "flask"

        # 与上面相同，但保留了客户端上下文
        with client:
            client.get().close()

        with client, client.get() as rv:
            assert rv.text == "flask"


class TestHelpers:
    @pytest.mark.parametrize(
        ("debug", "expect"),
        [
            ("", False),
            ("0", False),
            ("False", False),
            ("No", False),
            ("True", True),
        ],
    )
    def test_get_debug_flag(self, monkeypatch, debug, expect):
        monkeypatch.setenv("FLASK_DEBUG", debug)
        assert get_debug_flag() == expect

    def test_make_response(self):
        app = flask.Flask(__name__)
        with app.test_request_context():
            rv = flask.helpers.make_response()
            assert rv.status_code == 200
            assert rv.mimetype == "text/html"

            rv = flask.helpers.make_response("Hello")
            assert rv.status_code == 200
            assert rv.data == b"Hello"
            assert rv.mimetype == "text/html"


@pytest.mark.parametrize("mode", ("r", "rb", "rt"))
def test_open_resource(mode):
    app = flask.Flask(__name__)

    with app.open_resource("static/index.html", mode) as f:
        assert "<h1>Hello World!</h1>" in str(f.read())


@pytest.mark.parametrize("mode", ("w", "x", "a", "r+"))
def test_open_resource_exceptions(mode):
    app = flask.Flask(__name__)

    with pytest.raises(ValueError):
        app.open_resource("static/index.html", mode)


@pytest.mark.parametrize("encoding", ("utf-8", "utf-16-le"))
def test_open_resource_with_encoding(tmp_path, encoding):
    app = flask.Flask(__name__, root_path=os.fspath(tmp_path))
    (tmp_path / "test").write_text("test", encoding=encoding)

    with app.open_resource("test", mode="rt", encoding=encoding) as f:
        assert f.read() == "test"
