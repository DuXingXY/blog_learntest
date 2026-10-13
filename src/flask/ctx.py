from __future__ import annotations

import contextvars
import typing as t
from functools import update_wrapper
from types import TracebackType

from werkzeug.exceptions import HTTPException
from werkzeug.routing import MapAdapter

from . import typing as ft
from .globals import _cv_app
from .helpers import _CollectErrors
from .signals import appcontext_popped
from .signals import appcontext_pushed

if t.TYPE_CHECKING:
    import typing_extensions as te
    from _typeshed.wsgi import WSGIEnvironment

    from .app import Flask
    from .sessions import SessionMixin
    from .wrappers import Request


# 参数默认值的单例哨兵值
_sentinel = object()


class _AppCtxGlobals:
    """一个普通的物体。用作在应用程序上下文期间存储数据的命名空间。

    创建应用程序上下文会自动创建此对象，该对象可用作 :data:`.g` 代理。

    .. describe:: 'key' in g

        检查属性是否存在。

        .. versionadded:: 0.10

    .. describe:: iter(g)

        返回属性名称的迭代器。

        .. versionadded:: 0.10
    """

    # 定义 attr 方法让 mypy 知道这是一个具有任意属性的命名空间对象。

    def __getattr__(self, name: str) -> t.Any:
        try:
            return self.__dict__[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: t.Any) -> None:
        self.__dict__[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self.__dict__[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, name: str, default: t.Any | None = None) -> t.Any:
        """按名称或默认值获取属性。就像 :meth:`dict.get` 一样。

        :param name: 要获取的属性名称。
        :param default: 如果属性不存在则返回值。

        .. versionadded:: 0.10
        """
        return self.__dict__.get(name, default)

    def pop(self, name: str, default: t.Any = _sentinel) -> t.Any:
        """按名称获取和删除属性。就像 :meth:`dict.pop` 一样。

        :param name: 要弹出的属性名称。
        :param default: 如果属性不存在则返回值，而不是引发 ``KeyError`` 。

        .. versionadded:: 0.11
        """
        if default is _sentinel:
            return self.__dict__.pop(name)
        else:
            return self.__dict__.pop(name, default)

    def setdefault(self, name: str, default: t.Any = None) -> t.Any:
        """获取属性的值（如果存在），否则设置并返回默认值。就像 :meth:`dict.setdefault`
        一样。

        :param name: 要获取的属性名称。
        :param default: 如果属性不存在，则设置并返回的值。

        .. versionadded:: 0.11
        """
        return self.__dict__.setdefault(name, default)

    def __contains__(self, item: str) -> bool:
        return item in self.__dict__

    def __iter__(self) -> t.Iterator[str]:
        return iter(self.__dict__)

    def __repr__(self) -> str:
        ctx = _cv_app.get(None)
        if ctx is not None:
            return f"<flask.g of '{ctx.app.name}'>"
        return object.__repr__(self)


def after_this_request(
    f: ft.AfterRequestCallable[t.Any],
) -> ft.AfterRequestCallable[t.Any]:
    """装饰一个函数以在当前请求之后运行。该行为与 :meth:`.Flask.after_request` 相同，但
    它仅适用于当前请求，而不是每个请求。因此，它必须在请求上下文中使用，而不是在设置期间
    使用。

    .. code-block:: python

        @app.route("/")
        def index():
            @after_this_request
            def add_header(response):
                response.headers["X-Foo"] = "Parachute"
                return response

            return "Hello, World!"

    .. versionadded:: 0.9
    """
    ctx = _cv_app.get(None)

    if ctx is None or not ctx.has_request:
        raise RuntimeError(
            "'after_this_request' can only be used when a request"
            " context is active, such as in a view function."
        )

    ctx._after_request_functions.append(f)
    return f


F = t.TypeVar("F", bound=t.Callable[..., t.Any])


def copy_current_request_context(f: F) -> F:
    """装饰一个函数以在当前请求上下文中运行。这可以在启动后台任务时使用，否则它将看不到
    父级中活动的应用程序和请求对象。

    .. warning::

        由于以下注意事项，在启动任务时传递所需的数据通常更安全（也更简单），而不是使用它
        并依赖于上下文对象。

    为了避免通过读取数据部分执行切换，请在开始任务之前读取请求正文（访问 ``form`` 、
    ``json`` 、 ``data`` 等），或者使用锁。使用线程时这可能是一个问题，但使用 greenlet/
    gevent 或 asyncio 时不应该是问题。

    如果任务将访问 ``session`` ，请确保也在父级中执行此操作，以便设置 ``Vary: cookie``
    标头。应避免在任务中修改 ``session`` ，因为它可能会在响应 cookie 写入后执行。

    .. code-block:: python

        import gevent
        from flask import copy_current_request_context

        @app.route('/')
        def index():
            @copy_current_request_context
            def do_some_work():
                # 在这里做一些工作，它可以访问 flask.request 或
                # flask.session 就像在视图函数中那样。
                ...
            gevent.spawn(do_some_work)
            return 'Regular response'

    .. versionadded:: 0.10
    """
    # 存储应用装饰器时处于活动状态的上下文。
    original = _cv_app.get(None)

    if original is None:
        raise RuntimeError(
            "'copy_current_request_context' can only be used when a"
            " request context is active, such as in a view function."
        )

    def wrapper(*args: t.Any, **kwargs: t.Any) -> t.Any:
        # 在推送之前复制上下文，以便每个工作人员独立操作。
        with original.copy() as ctx:
            return ctx.app.ensure_sync(f)(*args, **kwargs)

    return update_wrapper(wrapper, f)  # type: ignore[return-value]


def has_request_context() -> bool:
    """测试应用程序上下文是否处于活动状态以及是否有请求信息。

    .. code-block:: python

        from flask import has_request_context, request

        if has_request_context():
            remote_addr = request.remote_addr

    如果请求上下文处于活动状态，则 :data:`.request` 和 :data:`.session` 上下文代理将可用
    ，并且 ``True`` 可用，否则为 ``False`` 。您可以使用它来测试您使用的数据，而不是使用
    此函数。

    .. code-block:: python

        from flask import request

        if request:
            remote_addr = request.remote_addr

    .. versionadded:: 0.7
    """
    return (ctx := _cv_app.get(None)) is not None and ctx.has_request


def has_app_context() -> bool:
    """测试应用程序上下文是否处于活动状态。与 :func:`has_request_context` 不同，这可以在
    请求之外为真，例如在 CLI 命令中。

    .. code-block:: python

        from flask import has_app_context, g

        if has_app_context():
            g.cached_data = ...

    如果应用程序上下文处于活动状态，则 :data:`.g` 和 :data:`.current_app` 上下文代理将可
    用，并且 ``True`` ，否则为 ``False`` 。您可以使用它来测试您使用的数据，而不是使用此
    函数。

        from flask import g

        如果 g：
            g.cached_data = ...

    .. versionadded:: 0.9
    """
    return _cv_app.get(None) is not None


class AppContext:
    """应用程序上下文包含有关应用程序以及处理请求时的请求的信息。在每个请求和 CLI 命令的
    开头推送上下文，并在末尾弹出。如果上下文有请求信息，则称为 "request context"，如果没
    有，则称为 "app context"。

    不要直接使用此类。如果在设置期间需要，请使用 :meth:`.Flask.app_context` 创建应用程序
    上下文，如果在测试期间需要，请使用 :meth:`.Flask.test_request_context` 创建请求上下
    文。

    当弹出上下文时，它将评估使用 :meth:`~flask.Flask.teardown_request` 注册的所有清理函
    数（如果处理请求），然后评估 :meth:`.Flask.teardown_appcontext` 。

    使用交互式调试器时，上下文将被恢复，因此 ``request`` 仍然可以访问。同样，测试客户端
    可以在请求结束后保留上下文。但是，清理函数可能已经关闭了一些资源（例如数据库连接），
    并且在弹出恢复的上下文时将再次运行。

    :param app: 此上下文代表的应用程序。
    :param request: 该上下文代表的请求数据。
    :param session: 此上下文表示的会话数据。如果未给出，则在第一次访问时从请求加载。

    .. versionchanged:: 3.2
        与 ``RequestContext`` 合并。 ``RequestContext`` 别名将在 Flask 4.0 中删除。

    .. versionchanged:: 3.2
        为每个请求和 CLI 命令推送组合的应用程序和请求上下文，而不是尝试检测应用程序上下
        文是否已推送。

    .. versionchanged:: 3.2
        会话在第一次访问时加载，而不是在推送上下文时加载。
    """

    def __init__(
        self,
        app: Flask,
        *,
        request: Request | None = None,
        session: SessionMixin | None = None,
    ) -> None:
        self.app = app
        """此上下文代表的应用程序。通过 :data:`.current_app` 访问。
        """

        self.g: _AppCtxGlobals = app.app_ctx_globals_class()
        """此上下文的全局数据。通过 :data:`.g` 访问。"""

        self.url_adapter: MapAdapter | None = None
        """URL 适配器绑定到请求，或者应用程序（如果不在请求中）。如果绑定失败，可能是
        ``None`` 。
        """

        self._request: Request | None = request
        self._session: SessionMixin | None = session
        self._flashes: list[tuple[str, str]] | None = None
        self._after_request_functions: list[ft.AfterRequestCallable[t.Any]] = []

        try:
            self.url_adapter = app.create_url_adapter(self._request)
        except HTTPException as e:
            if self._request is not None:
                self._request.routing_exception = e

        self._cv_token: contextvars.Token[AppContext] | None = None
        """弹出时恢复之前的状态。"""

        self._push_count: int = 0
        """跟踪此上下文的嵌套推送。只有在弹出原始推送后才会运行清理操作。
        """

    @classmethod
    def from_environ(cls, app: Flask, environ: WSGIEnvironment, /) -> te.Self:
        """使用给定 WSGI 环境中的请求数据创建应用程序上下文。

        :param app: 此上下文代表的应用程序。
        :param environ: 该上下文代表的请求数据。
        """
        request = app.request_class(environ)
        request.json_module = app.json
        return cls(app, request=request)

    @property
    def has_request(self) -> bool:
        """True 如果此上下文是使用请求数据创建的。"""
        return self._request is not None

    def copy(self) -> te.Self:
        """使用与此上下文相同的数据对象创建一个新上下文。请参阅
        :func:`.copy_current_request_context` 。

        .. versionchanged:: 1.1
            使用当前会话数据而不是重新加载原始数据。

        .. versionadded:: 0.10
        """
        return self.__class__(
            self.app,
            request=self._request,
            session=self._session,
        )

    @property
    def request(self) -> Request:
        """与此上下文关联的请求对象。通过 :data:`.request` 访问。仅在请求上下文中可用，
        否则引发 :exc:`RuntimeError` 。
        """
        if self._request is None:
            raise RuntimeError("There is no request in this context.")

        return self._request

    def _get_session(self) -> SessionMixin:
        """如果尚未为此请求上下文打开会话，则打开会话。"""
        if self._request is None:
            raise RuntimeError("There is no request in this context.")

        if self._session is None:
            si = self.app.session_interface
            self._session = si.open_session(self.app, self.request)

            if self._session is None:
                self._session = si.make_null_session(self.app)

        return self._session

    @property
    def session(self) -> SessionMixin:
        """与此上下文关联的会话对象。通过 :data:`.session` 访问。仅在请求上下文中可用，
        否则引发 :exc:`RuntimeError` 。访问此集 :attr:`.SessionMixin.accessed` 。
        """
        session = self._get_session()
        session.accessed = True
        return session

    def match_request(self) -> None:
        """将路由应用于当前请求，存储匹配的端点和参数，或者存储路由异常。"""
        try:
            result = self.url_adapter.match(return_rule=True)  # type: ignore[union-attr]
        except HTTPException as e:
            self._request.routing_exception = e  # type: ignore[union-attr]
        else:
            self._request.url_rule, self._request.view_args = result  # type: ignore[union-attr]

    def push(self) -> None:
        """推送此上下文，使其成为活动上下文。如果这是一个请求上下文，则调用
        :meth:`match_request` 以在上下文处于活动状态时执行路由。

        通常，这不直接使用。相反，使用 ``with`` 块来管理上下文。

        在某些情况下，例如流式传输或测试，上下文可能会被推送多次。如果当前没有推送，它只
        会触发匹配和信号。
        """
        self._push_count += 1

        if self._cv_token is not None:
            return

        self._cv_token = _cv_app.set(self)
        appcontext_pushed.send(self.app, _async_wrapper=self.app.ensure_sync)

        if self._request is not None:
            # 在请求上下文可用时打开会话。这允许自定义 open_session 方法使用请求上下文。
            self._get_session()

            # 加载会话后匹配请求 URL，以便会话在自定义 URL 转换器中可用。
            if self.url_adapter is not None:
                self.match_request()

    def pop(self, exc: BaseException | None = None) -> None:
        """弹出此上下文，使其不再是活动上下文。然后调用清理函数和信号。

        通常，这不直接使用。相反，使用 ``with`` 块来管理上下文。

        该上下文当前必须是活动上下文，否则会引发 :exc:`RuntimeError` 。在某些情况下，例
        如流式传输或测试，上下文可能已被推送多次。只有当它被弹出的次数与被推送的次数相同
        时，它才会触发清理。在那之前，它将仍然是活动上下文。

        :param exc: 上下文处于活动状态时引发的未处理的异常。传递给清理函数。

        .. versionchanged:: 0.9
            添加了 ``exc`` 参数。
        """
        if self._cv_token is None:
            raise RuntimeError(f"Cannot pop this context ({self!r}), it is not pushed.")

        ctx = _cv_app.get(None)

        if ctx is None or self._cv_token is None:
            raise RuntimeError(
                f"Cannot pop this context ({self!r}), there is no active context."
            )

        if ctx is not self:
            raise RuntimeError(
                f"Cannot pop this context ({self!r}), it is not the active"
                f" context ({ctx!r})."
            )

        self._push_count -= 1

        if self._push_count > 0:
            return

        collect_errors = _CollectErrors()

        if self._request is not None:
            with collect_errors:
                self.app.do_teardown_request(self, exc)

            with collect_errors:
                self._request.close()

        with collect_errors:
            self.app.do_teardown_appcontext(self, exc)

        _cv_app.reset(self._cv_token)
        self._cv_token = None

        with collect_errors:
            appcontext_popped.send(self.app, _async_wrapper=self.app.ensure_sync)

        collect_errors.raise_any("Errors during context teardown")

    def __enter__(self) -> te.Self:
        self.push()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.pop(exc_value)

    def __repr__(self) -> str:
        if self._request is not None:
            return (
                f"<{type(self).__name__} {id(self)} of {self.app.name},"
                f" {self.request.method} {self.request.url!r}>"
            )

        return f"<{type(self).__name__} {id(self)} of {self.app.name}>"


def __getattr__(name: str) -> t.Any:
    import warnings

    if name == "RequestContext":
        warnings.warn(
            "'RequestContext' has merged with 'AppContext', and will be removed"
            " in Flask 4.0. Use 'AppContext' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return AppContext

    raise AttributeError(name)
