from __future__ import annotations

import importlib.metadata
import typing as t
from contextlib import contextmanager
from contextlib import ExitStack
from copy import copy
from types import TracebackType
from urllib.parse import urlsplit

import werkzeug.test
from click.testing import CliRunner
from click.testing import Result
from werkzeug.test import Client
from werkzeug.wrappers import Request as BaseRequest

from .cli import ScriptInfo
from .sessions import SessionMixin

if t.TYPE_CHECKING:  # pragma: no cover
    from _typeshed.wsgi import WSGIEnvironment
    from werkzeug.test import TestResponse

    from .app import Flask


class EnvironBuilder(werkzeug.test.EnvironBuilder):
    """:class:`~werkzeug.test.EnvironBuilder` ，从应用程序获取默认值。

    :param app: 用于配置环境的 Flask 应用程序。
    :param path: 正在请求 URL 路径。
    :param base_url: 提供应用程序的基础 URL，与 ``path`` 相关。如果未给出，则从
        :data:`PREFERRED_URL_SCHEME` 、 ``subdomain`` 、 :data:`SERVER_NAME` 和
        :data:`APPLICATION_ROOT` 构建。
    :param subdomain: 要附加到 :data:`SERVER_NAME` 的子域名。
    :param url_scheme: 使用方案代替 :data:`PREFERRED_URL_SCHEME` 。
    :param json: 如果给定，则将其序列化为 JSON 并作为 ``data`` 传递。还将
        ``content_type`` 默认为 ``application/json`` 。
    :param args: 其他位置参数传递给 :class:`~werkzeug.test.EnvironBuilder` 。
    :param kwargs: 其他关键字参数传递给 :class:`~werkzeug.test.EnvironBuilder` 。
    """

    def __init__(
        self,
        app: Flask,
        path: str = "/",
        base_url: str | None = None,
        subdomain: str | None = None,
        url_scheme: str | None = None,
        *args: t.Any,
        **kwargs: t.Any,
    ) -> None:
        assert not (base_url or subdomain or url_scheme) or (
            base_url is not None
        ) != bool(subdomain or url_scheme), (
            'Cannot pass "subdomain" or "url_scheme" with "base_url".'
        )

        if base_url is None:
            http_host = app.config.get("SERVER_NAME") or "localhost"
            app_root = app.config["APPLICATION_ROOT"]

            if subdomain:
                http_host = f"{subdomain}.{http_host}"

            if url_scheme is None:
                url_scheme = app.config["PREFERRED_URL_SCHEME"]

            url = urlsplit(path)
            base_url = (
                f"{url.scheme or url_scheme}://{url.netloc or http_host}"
                f"/{app_root.lstrip('/')}"
            )
            path = url.path

            if url.query:
                path = f"{path}?{url.query}"

        self.app = app
        super().__init__(path, base_url, *args, **kwargs)

    def json_dumps(self, obj: t.Any, **kwargs: t.Any) -> str:
        """将 ``obj`` 序列化为 JSON 格式的字符串。

        序列化将根据与此 EnvironBuilder 的 ``app`` 关联的配置进行配置。
        """
        return self.app.json.dumps(obj, **kwargs)


_werkzeug_version = ""


def _get_werkzeug_version() -> str:
    global _werkzeug_version

    if not _werkzeug_version:
        _werkzeug_version = importlib.metadata.version("werkzeug")

    return _werkzeug_version


class FlaskClient(Client):
    """与常规 Werkzeug 测试客户端类似，但具有 Flask 的附加行为。可以将请求上下文的清理推
    迟到 ``with`` 块结束时。有关如何使用此类的一般信息，请参阅
    :class:`werkzeug.test.Client` 。

    .. versionchanged:: 0.12
       `app.test_client()` 包含预设的默认环境，可以在 `client.environ_base` 中实例化
       `app.test_client()` 对象后设置。

    :doc:`/testing` 章节中概述了基本用法。
    """

    application: Flask

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)
        self.preserve_context = False
        self._new_contexts: list[t.ContextManager[t.Any]] = []
        self._context_stack = ExitStack()
        self.environ_base = {
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_USER_AGENT": f"Werkzeug/{_get_werkzeug_version()}",
        }

    @contextmanager
    def session_transaction(
        self, *args: t.Any, **kwargs: t.Any
    ) -> t.Iterator[SessionMixin]:
        """当与 ``with`` 语句结合使用时，这将打开一个会话事务。  这可用于修改测试客户端
        使用的会话。  一旦离开 ``with`` 块，会话就会被存储回来。

        ::

            with client.session_transaction() as session:
                session['value'] = 42

        在内部，这是通过遍历临时测试请求上下文来实现的，并且由于会话处理可能依赖于请求变
        量，因此该函数接受与直接传递的 :meth:`~flask.Flask.test_request_context` 相同的
        参数。
        """
        if self._cookies is None:
            raise TypeError(
                "Cookies are disabled. Create a client with 'use_cookies=True'."
            )

        app = self.application
        ctx = app.test_request_context(*args, **kwargs)
        self._add_cookies_to_wsgi(ctx.request.environ)

        with ctx:
            sess = app.session_interface.open_session(app, ctx.request)

        if sess is None:
            raise RuntimeError("Session backend did not open a session.")

        yield sess
        resp = app.response_class()

        if app.session_interface.is_null_session(sess):
            return

        with ctx:
            app.session_interface.save_session(app, sess, resp)

        self._update_cookies_from_response(
            urlsplit(ctx.request.host_url).hostname or "localhost",
            ctx.request.path,
            resp.headers.getlist("Set-Cookie"),
        )

    def _copy_environ(self, other: WSGIEnvironment) -> WSGIEnvironment:
        out = {**self.environ_base, **other}

        if self.preserve_context:
            out["werkzeug.debug.preserve_context"] = self._new_contexts.append

        return out

    def _request_from_builder_args(
        self, args: tuple[t.Any, ...], kwargs: dict[str, t.Any]
    ) -> BaseRequest:
        kwargs["environ_base"] = self._copy_environ(kwargs.get("environ_base", {}))
        builder = EnvironBuilder(self.application, *args, **kwargs)

        try:
            return builder.get_request()
        finally:
            builder.close()

    def open(
        self,
        *args: t.Any,
        buffered: bool = False,
        follow_redirects: bool = False,
        **kwargs: t.Any,
    ) -> TestResponse:
        if args and isinstance(
            args[0], (werkzeug.test.EnvironBuilder, dict, BaseRequest)
        ):
            if isinstance(args[0], werkzeug.test.EnvironBuilder):
                builder = copy(args[0])
                builder.environ_base = self._copy_environ(builder.environ_base or {})  # type: ignore[arg-type]
                request = builder.get_request()
            elif isinstance(args[0], dict):
                request = EnvironBuilder.from_environ(
                    args[0], app=self.application, environ_base=self._copy_environ({})
                ).get_request()
            else:
                # isinstance(args[0], BaseRequest)
                request = copy(args[0])
                request.environ = self._copy_environ(request.environ)
        else:
            # 请求是 None
            request = self._request_from_builder_args(args, kwargs)

        # 弹出任何以前保留的上下文。这可以防止跨重定向或单个块中的多个请求保留上下文。
        self._context_stack.close()

        response = super().open(
            request,
            buffered=buffered,
            follow_redirects=follow_redirects,
        )
        response.json_module = self.application.json  # type: ignore[assignment]

        # 重新推送请求期间保留的上下文。
        for cm in self._new_contexts:
            self._context_stack.enter_context(cm)

        self._new_contexts.clear()
        return response

    def __enter__(self) -> FlaskClient:
        if self.preserve_context:
            raise RuntimeError("Cannot nest client invocations")
        self.preserve_context = True
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.preserve_context = False
        self._context_stack.close()


class FlaskCliRunner(CliRunner):
    """:class:`~click.testing.CliRunner` 用于测试 Flask 应用程序的 CLI 命令。通常使用
    :meth:`~flask.Flask.test_cli_runner` 创建。参见 :ref:`testing-cli` 。
    """

    def __init__(self, app: Flask, **kwargs: t.Any) -> None:
        self.app = app
        super().__init__(**kwargs)

    def invoke(  # type: ignore
        self, cli: t.Any = None, args: t.Any = None, **kwargs: t.Any
    ) -> Result:
        """在隔离环境中调用 CLI 命令。有关完整方法文档，请参阅
        :meth:`CliRunner.invoke <click.testing.CliRunner.invoke>` 。有关示例，请参阅
        :ref:`testing-cli` 。

        如果未给出 ``obj`` 参数，则传递知道如何加载正在测试的 Flask 应用程序的
        :class:`~flask.cli.ScriptInfo` 实例。

        :param cli: 要调用的命令对象。默认是应用程序的 :attr:`~flask.app.Flask.cli` 组。
        :param args: 用于调用命令的字符串列表。

        :return: 一个 :class:`~click.testing.Result` 对象。
        """
        if cli is None:
            cli = self.app.cli

        if "obj" not in kwargs:
            kwargs["obj"] = ScriptInfo(create_app=lambda: self.app)

        return super().invoke(cli, args, **kwargs)
