from __future__ import annotations

import typing as t

from . import typing as ft
from .globals import current_app
from .globals import request

F = t.TypeVar("F", bound=t.Callable[..., t.Any])

http_method_funcs = frozenset(
    ["get", "query", "post", "head", "options", "delete", "put", "trace", "patch"]
)


class View:
    """对该类进行子类化并覆盖 :meth:`dispatch_request` 以创建通用的基于类的视图。调用
    :meth:`as_view` 创建一个视图函数，该函数使用给定参数创建类的实例，并使用任何 URL 变
    量调用其 ``dispatch_request`` 方法。

    有关详细指南，请参阅 :doc:`views` 。

    .. code-block:: python

        class Hello(View):
            init_every_request = False

            def dispatch_request(self, name):
                return f"Hello, {name}!"

        app.add_url_rule(
            "/hello/<name>", view_func=Hello.as_view("hello")
        )

    在类上设置 :attr:`methods` 以更改视图接受的方法。

    在类上设置 :attr:`decorators` 以将装饰器列表应用于生成的视图函数。应用于类本身的装饰
    器不会应用于生成的视图函数！

    将 :attr:`init_every_request` 设置为 ``False`` 以提高效率，除非您需要在 ``self`` 上
    存储请求全局数据。
    """

    #: 该视图注册的方法。默认情况下使用与 ``route`` 和 ``add_url_rule`` 相同的默认值 (
    #: ``["GET", "HEAD", "OPTIONS"]`` )。
    methods: t.ClassVar[t.Collection[str] | None] = None

    #: 控制是否自动处理 ``OPTIONS`` 方法。默认情况下使用与 ``route`` 和 ``add_url_rule``
    #: 相同的默认值 ( ``True`` )。
    provide_automatic_options: t.ClassVar[bool | None] = None

    #: 按顺序应用于生成的视图函数的装饰器列表。请记住， ``@decorator`` 语法是从下到上应
    #: 用的，因此列表中的第一个装饰器将是底部装饰器。
    #:
    #: .. versionadded:: 0.8
    decorators: t.ClassVar[list[t.Callable[..., t.Any]]] = []

    #: 默认情况下，为每个请求创建此视图类的新实例。如果视图子类将其设置为 ``False`` ，则
    #: 每个请求都会使用相同的实例。
    #:
    #: 单个实例效率更高，特别是在 init 期间完成复杂的设置时。但是，在 ``self`` 上存储数
    #: 据在跨请求时不再安全，应改用 :data:`~flask.g` 。
    #:
    #: .. versionadded:: 2.2
    init_every_request: t.ClassVar[bool] = True

    def dispatch_request(self) -> ft.ResponseReturnValue:
        """实际的视图函数行为。子类必须重写它并返回有效的响应。 URL 规则中的任何变量都作
        为关键字参数传递。
        """
        raise NotImplementedError()

    @classmethod
    def as_view(
        cls, name: str, *class_args: t.Any, **class_kwargs: t.Any
    ) -> ft.RouteCallable:
        """将类转换为可以注册路由的视图函数。

        默认情况下，生成的视图将为每个请求创建视图类的新实例并调用其
        :meth:`dispatch_request` 方法。如果视图类将 :attr:`init_every_request` 设置为
        ``False`` ，则每个请求都将使用相同的实例。

        除了 ``name`` 之外，传递给此方法的所有其他参数都将转发到视图类 ``__init__`` 方法
        。

        .. versionchanged:: 2.2
            添加了 ``init_every_request`` 类属性。
        """
        if cls.init_every_request:

            def view(**kwargs: t.Any) -> ft.ResponseReturnValue:
                self = view.view_class(  # type: ignore[attr-defined]
                    *class_args, **class_kwargs
                )
                return current_app.ensure_sync(self.dispatch_request)(**kwargs)  # type: ignore[no-any-return]

        else:
            self = cls(*class_args, **class_kwargs)  # pyright: ignore

            def view(**kwargs: t.Any) -> ft.ResponseReturnValue:
                return current_app.ensure_sync(self.dispatch_request)(**kwargs)  # type: ignore[no-any-return]

        if cls.decorators:
            view.__name__ = name
            view.__module__ = cls.__module__
            for decorator in cls.decorators:
                view = decorator(view)

        # 我们将视图类附加到视图函数有两个原因：首先，它使我们能够轻松地弄清楚这个东西来
        # 自哪个基于类的视图，其次，它还用于实例化视图类，因此您实际上可以用其他东西替换
        # 它以进行测试和调试。
        view.view_class = cls  # type: ignore
        view.__name__ = name
        view.__doc__ = cls.__doc__
        view.__module__ = cls.__module__
        view.methods = cls.methods  # type: ignore
        view.provide_automatic_options = cls.provide_automatic_options  # type: ignore
        return view


class MethodView(View):
    """将请求方法分派给相应的实例方法。例如，如果您实现 ``get`` 方法，它将用于处理
    ``GET`` 请求。

    这对于定义 REST API 很有用。

    :attr:`methods` 根据类上定义的方法自动设置。

    有关详细指南，请参阅 :doc:`views` 。

    .. code-block:: python

        class CounterAPI(MethodView):
            def get(self):
                return str(session.get("counter", 0))

            def post(self):
                session["counter"] = session.get("counter", 0) + 1
                return redirect(url_for("counter"))

        app.add_url_rule(
            "/counter", view_func=CounterAPI.as_view("counter")
        )
    """

    def __init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__init_subclass__(**kwargs)

        if "methods" not in cls.__dict__:
            methods = set()

            for base in cls.__bases__:
                if getattr(base, "methods", None):
                    methods.update(base.methods)  # type: ignore[attr-defined]

            for key in http_method_funcs:
                if hasattr(cls, key):
                    methods.add(key.upper())

            if methods:
                cls.methods = methods

    def dispatch_request(self, **kwargs: t.Any) -> ft.ResponseReturnValue:
        meth = getattr(self, request.method.lower(), None)

        # 如果请求方法是 HEAD 并且我们没有处理程序，请使用 GET 重试。
        if meth is None and request.method == "HEAD":
            meth = getattr(self, "get", None)

        assert meth is not None, f"Unimplemented method {request.method!r}"
        return current_app.ensure_sync(meth)(**kwargs)  # type: ignore[no-any-return]
