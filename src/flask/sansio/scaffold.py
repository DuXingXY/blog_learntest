from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import typing as t
from collections import defaultdict
from functools import update_wrapper

from jinja2 import BaseLoader
from jinja2 import FileSystemLoader
from werkzeug.exceptions import default_exceptions
from werkzeug.exceptions import HTTPException
from werkzeug.utils import cached_property

from .. import typing as ft
from ..helpers import get_root_path
from ..templating import _default_template_ctx_processor

if t.TYPE_CHECKING:  # pragma: no cover
    from click import Group

# 参数默认值的单例哨兵值
_sentinel = object()

F = t.TypeVar("F", bound=t.Callable[..., t.Any])
T_after_request = t.TypeVar("T_after_request", bound=ft.AfterRequestCallable[t.Any])
T_before_request = t.TypeVar("T_before_request", bound=ft.BeforeRequestCallable)
T_error_handler = t.TypeVar("T_error_handler", bound=ft.ErrorHandlerCallable)
T_teardown = t.TypeVar("T_teardown", bound=ft.TeardownCallable)
T_template_context_processor = t.TypeVar(
    "T_template_context_processor", bound=ft.TemplateContextProcessorCallable
)
T_url_defaults = t.TypeVar("T_url_defaults", bound=ft.URLDefaultCallable)
T_url_value_preprocessor = t.TypeVar(
    "T_url_value_preprocessor", bound=ft.URLValuePreprocessorCallable
)
T_route = t.TypeVar("T_route", bound=ft.RouteCallable)


def setupmethod(f: F) -> F:
    f_name = f.__name__

    def wrapper_func(self: Scaffold, *args: t.Any, **kwargs: t.Any) -> t.Any:
        self._check_setup_finished(f_name)
        return f(self, *args, **kwargs)

    return t.cast(F, update_wrapper(wrapper_func, f))


class Scaffold:
    """:class:`~flask.Flask` 和 :class:`~flask.blueprints.Blueprint` 之间共享的常见行为
    。

    :param import_name: 定义此对象的模块的导入名称。通常应该使用 :attr:`__name__` 。
    :param static_folder: 要提供服务的静态文件文件夹的路径。如果设置了此项，将添加静态路
        由。
    :param static_url_path: 静态路由的 URL 前缀。
    :param template_folder: 包含模板文件的文件夹的路径。用于渲染。如果设置了此选项，则会
        添加 Jinja 加载程序。
    :param root_path: 静态文件、模板文件和资源文件的相对路径。通常不设置，它是基于
        ``import_name`` 发现的。

    .. versionadded:: 2.0
    """

    cli: Group
    name: str
    _static_folder: str | None = None
    _static_url_path: str | None = None

    def __init__(
        self,
        import_name: str,
        static_folder: str | os.PathLike[str] | None = None,
        static_url_path: str | None = None,
        template_folder: str | os.PathLike[str] | None = None,
        root_path: str | None = None,
    ):
        #: 该对象所属的包或模块的名称。一旦由构造函数设置，就不要更改它。
        self.import_name = import_name

        self.static_folder = static_folder
        self.static_url_path = static_url_path

        #: 要添加到模板加载器的模板文件夹的路径（相对于 :attr:`root_path` ）。 ``None``
        #: 如果不应添加模板。
        self.template_folder = template_folder

        if root_path is None:
            root_path = get_root_path(self.import_name)

        #: 文件系统上包的绝对路径。用于查找包中包含的资源。
        self.root_path = root_path

        #: 将端点名称映射到视图函数的字典。
        #:
        #: 要注册视图函数，请使用 :meth:`route` 装饰器。
        #:
        #: 该数据结构是内部的。不应直接修改它，其格式可能随时更改。
        self.view_functions: dict[str, ft.RouteCallable] = {}

        #: 已注册错误处理程序的数据结构，格式为 ``{scope: {code: {class: handler}}}`` 。
        #: ``scope`` 键是处理程序处于活动状态的蓝图的名称，或 ``None`` 键是所有请求的名
        #: 称。 ``code`` 键是 ``HTTPException`` 的 HTTP 状态代码，或者其他异常的
        #: ``None`` 状态代码。最里面的字典将异常类映射到处理函数。
        #:
        #: 要注册错误处理程序，请使用 :meth:`errorhandler` 装饰器。
        #:
        #: 该数据结构是内部的。不应直接修改它，其格式可能随时更改。
        self.error_handler_spec: dict[
            ft.AppOrBlueprintKey,
            dict[int | None, dict[type[Exception], ft.ErrorHandlerCallable]],
        ] = defaultdict(lambda: defaultdict(dict))

        #: 在每个请求开始时调用的函数的数据结构，格式为 ``{scope: [functions]}`` 。
        #: ``scope`` 键是函数处于活动状态的蓝图的名称，或 ``None`` 键是所有请求的名称。
        #:
        #: 要注册函数，请使用 :meth:`before_request` 装饰器。
        #:
        #: 该数据结构是内部的。不应直接修改它，其格式可能随时更改。
        self.before_request_funcs: dict[
            ft.AppOrBlueprintKey, list[ft.BeforeRequestCallable]
        ] = defaultdict(list)

        #: 在每个请求结束时调用的函数的数据结构，格式为 ``{scope: [functions]}`` 。
        #: ``scope`` 键是函数处于活动状态的蓝图的名称，或 ``None`` 键是所有请求的名称。
        #:
        #: 要注册函数，请使用 :meth:`after_request` 装饰器。
        #:
        #: 该数据结构是内部的。不应直接修改它，其格式可能随时更改。
        self.after_request_funcs: dict[
            ft.AppOrBlueprintKey, list[ft.AfterRequestCallable[t.Any]]
        ] = defaultdict(list)

        #: 即使引发异常，也会在每个请求结束时调用的函数的数据结构，格式为
        #: ``{scope: [functions]}`` 。 ``scope`` 键是函数处于活动状态的蓝图的名称，或
        #: ``None`` 键是所有请求的名称。
        #:
        #: 要注册函数，请使用 :meth:`teardown_request` 装饰器。
        #:
        #: 该数据结构是内部的。不应直接修改它，其格式可能随时更改。
        self.teardown_request_funcs: dict[
            ft.AppOrBlueprintKey, list[ft.TeardownCallable]
        ] = defaultdict(list)

        #: 渲染模板时调用以传递额外上下文值的函数的数据结构，格式为
        #: ``{scope: [functions]}`` 。 ``scope`` 键是函数处于活动状态的蓝图的名称，或
        #: ``None`` 键是所有请求的名称。
        #:
        #: 要注册函数，请使用 :meth:`context_processor` 装饰器。
        #:
        #: 该数据结构是内部的。不应直接修改它，其格式可能随时更改。
        self.template_context_processors: dict[
            ft.AppOrBlueprintKey, list[ft.TemplateContextProcessorCallable]
        ] = defaultdict(list, {None: [_default_template_ctx_processor]})

        #: 调用函数的数据结构来修改传递给视图函数的关键字参数，格式为
        #: ``{scope: [functions]}`` 。 ``scope`` 键是函数处于活动状态的蓝图的名称，或
        #: ``None`` 键是所有请求的名称。
        #:
        #: 要注册函数，请使用 :meth:`url_value_preprocessor` 装饰器。
        #:
        #: 该数据结构是内部的。不应直接修改它，其格式可能随时更改。
        self.url_value_preprocessors: dict[
            ft.AppOrBlueprintKey,
            list[ft.URLValuePreprocessorCallable],
        ] = defaultdict(list)

        #: 生成 URL 时调用以修改关键字参数的函数的数据结构，格式为
        #: ``{scope: [functions]}`` 。 ``scope`` 键是函数处于活动状态的蓝图的名称，或
        #: ``None`` 键是所有请求的名称。
        #:
        #: 要注册函数，请使用 :meth:`url_defaults` 装饰器。
        #:
        #: 该数据结构是内部的。不应直接修改它，其格式可能随时更改。
        self.url_default_functions: dict[
            ft.AppOrBlueprintKey, list[ft.URLDefaultCallable]
        ] = defaultdict(list)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"

    def _check_setup_finished(self, f_name: str) -> None:
        raise NotImplementedError

    @property
    def static_folder(self) -> str | None:
        """配置的静态文件夹的绝对路径。如果未设置静态文件夹，则为 ``None`` 。"""
        if self._static_folder is not None:
            return os.path.join(self.root_path, self._static_folder)
        else:
            return None

    @static_folder.setter
    def static_folder(self, value: str | os.PathLike[str] | None) -> None:
        if value is not None:
            value = os.fspath(value).rstrip(r"\/")

        self._static_folder = value

    @property
    def has_static_folder(self) -> bool:
        """如果设置了 :attr:`static_folder` ，则为 ``True`` 。

        .. versionadded:: 0.5
        """
        return self.static_folder is not None

    @property
    def static_url_path(self) -> str | None:
        """可访问静态路由的 URL 前缀。

        如果在 init 期间未配置，则派生自 :attr:`static_folder` 。
        """
        if self._static_url_path is not None:
            return self._static_url_path

        if self.static_folder is not None:
            basename = os.path.basename(self.static_folder)
            return f"/{basename}".rstrip("/")

        return None

    @static_url_path.setter
    def static_url_path(self, value: str | None) -> None:
        if value is not None:
            value = value.rstrip("/")

        self._static_url_path = value

    @cached_property
    def jinja_loader(self) -> BaseLoader | None:
        """该对象模板的 Jinja 加载器。默认情况下，这是一个类
        :class:`jinja2.loaders.FileSystemLoader` 到 :attr:`template_folder` （如果已设置
        ）。

        .. versionadded:: 0.5
        """
        if self.template_folder is not None:
            return FileSystemLoader(os.path.join(self.root_path, self.template_folder))
        else:
            return None

    def _method_route(
        self,
        method: str,
        rule: str,
        options: dict[str, t.Any],
    ) -> t.Callable[[T_route], T_route]:
        if "methods" in options:
            raise TypeError("Use the 'route' decorator to use the 'methods' argument.")

        return self.route(rule, methods=[method], **options)

    @setupmethod
    def get(self, rule: str, **options: t.Any) -> t.Callable[[T_route], T_route]:
        """:meth:`route` 和 ``methods=["GET"]`` 的快捷方式。

        .. versionadded:: 2.0
        """
        return self._method_route("GET", rule, options)

    @setupmethod
    def query(self, rule: str, **options: t.Any) -> t.Callable[[T_route], T_route]:
        """:meth:`route` 和 ``methods=["QUERY"]`` 的快捷方式。

        .. versionadded:: 3.2
        """
        return self._method_route("QUERY", rule, options)

    @setupmethod
    def post(self, rule: str, **options: t.Any) -> t.Callable[[T_route], T_route]:
        """:meth:`route` 和 ``methods=["POST"]`` 的快捷方式。

        .. versionadded:: 2.0
        """
        return self._method_route("POST", rule, options)

    @setupmethod
    def put(self, rule: str, **options: t.Any) -> t.Callable[[T_route], T_route]:
        """:meth:`route` 和 ``methods=["PUT"]`` 的快捷方式。

        .. versionadded:: 2.0
        """
        return self._method_route("PUT", rule, options)

    @setupmethod
    def delete(self, rule: str, **options: t.Any) -> t.Callable[[T_route], T_route]:
        """:meth:`route` 和 ``methods=["DELETE"]`` 的快捷方式。

        .. versionadded:: 2.0
        """
        return self._method_route("DELETE", rule, options)

    @setupmethod
    def patch(self, rule: str, **options: t.Any) -> t.Callable[[T_route], T_route]:
        """:meth:`route` 和 ``methods=["PATCH"]`` 的快捷方式。

        .. versionadded:: 2.0
        """
        return self._method_route("PATCH", rule, options)

    @setupmethod
    def route(self, rule: str, **options: t.Any) -> t.Callable[[T_route], T_route]:
        """装饰视图函数以使用给定的 URL 规则和选项注册它。调用 :meth:`add_url_rule` ，其
        中包含有关实现的更多详细信息。

        .. code-block:: python

            @app.route("/")
            def index():
                return "Hello, World!"

        请参阅 :ref:`url-route-registrations` 。

        如果未传递 ``endpoint`` 参数，则路由的端点名称默认为视图函数的名称。

        ``methods`` 参数默认为 ``["GET"]`` 。 ``HEAD`` 和 ``OPTIONS`` 会自动添加。

        :param rule: URL 规则字符串。
        :param options: 传递给 :class:`~werkzeug.routing.Rule` 对象的额外选项。
        """

        def decorator(f: T_route) -> T_route:
            endpoint = options.pop("endpoint", None)
            self.add_url_rule(rule, endpoint, f, **options)
            return f

        return decorator

    @setupmethod
    def add_url_rule(
        self,
        rule: str,
        endpoint: str | None = None,
        view_func: ft.RouteCallable | None = None,
        provide_automatic_options: bool | None = None,
        **options: t.Any,
    ) -> None:
        """注册用于路由传入请求和构建 URL 的规则。 :meth:`route` 装饰器是使用
        ``view_func`` 参数调用它的快捷方式。这些是等效的：

        .. code-block:: python

            @app.route("/")
            def index():
                ...

        .. code-block:: python

            def index():
                ...

            app.add_url_rule("/", view_func=index)

        请参阅 :ref:`url-route-registrations` 。

        如果未传递 ``endpoint`` 参数，则路由的端点名称默认为视图函数的名称。如果已为端点
        注册了函数，则会引发错误。

        ``methods`` 参数默认为 ``["GET"]`` 。 ``HEAD`` 始终自动添加，而 ``OPTIONS`` 默认
        情况下自动添加。

        ``view_func`` 不一定需要传递，但如果规则应该参与路由，则端点名称必须在某个时刻通
        过 :meth:`endpoint` 装饰器与视图函数关联。

        .. code-block:: python

            app.add_url_rule("/", endpoint="index")

            @app.endpoint("index")
            def index():
                ...

        如果 ``view_func`` 具有 ``required_methods`` 属性，则这些方法将添加到传递方法和
        自动方法中。如果它具有 ``provide_automatic_methods`` 属性，则在未传递参数时将其
        用作默认值。

        :param rule: URL 规则字符串。
        :param endpoint: 与规则和查看功能关联的端点名称。在路由和构建 URL 时使用。默认为
            ``view_func.__name__`` 。
        :param view_func: 与端点名称关联的视图函数。
        :param provide_automatic_options: 添加 ``OPTIONS`` 方法并自动响应 ``OPTIONS`` 请
            求。
        :param options: 传递给 :class:`~werkzeug.routing.Rule` 对象的额外选项。
        """
        raise NotImplementedError

    @setupmethod
    def endpoint(self, endpoint: str) -> t.Callable[[F], F]:
        """装饰一个视图函数以将其注册到给定端点。如果添加的规则没有 ``view_func`` 和
        :meth:`add_url_rule` ，则使用。

        .. code-block:: python

            app.add_url_rule("/ex", endpoint="example")

            @app.endpoint("example")
            def example():
                ...

        :param endpoint: 与视图函数关联的端点名称。
        """

        def decorator(f: F) -> F:
            self.view_functions[endpoint] = f
            return f

        return decorator

    @setupmethod
    def before_request(self, f: T_before_request) -> T_before_request:
        """注册一个函数以在每个请求之前运行。

        例如，这可用于打开数据库连接，或从会话加载登录用户。

        .. code-block:: python

            @app.before_request
            def load_user():
                if "user_id" in session:
                    g.user = db.session.get(session["user_id"])

        该函数将在没有任何参数的情况下被调用。如果它返回非 ``None`` 值，则将该值视为视图
        的返回值，并停止进一步的请求处理。

        这在应用程序和蓝图对象上都可用。当在应用程序上使用时，它会在每个请求之前执行。当
        在蓝图上使用时，它在蓝图处理的每个请求之前执行。要注册蓝图并在每个请求之前执行，
        请使用 :meth:`.Blueprint.before_app_request` 。
        """
        self.before_request_funcs.setdefault(None, []).append(f)
        return f

    @setupmethod
    def after_request(self, f: T_after_request) -> T_after_request:
        """注册一个函数，在每次请求该对象后运行。

        该函数使用响应对象调用，并且必须返回响应对象。这允许函数在发送之前修改或替换响应
        。

        如果函数引发异常，则不会调用任何剩余的 ``after_request`` 函数。因此，这不应该用
        于必须执行的操作，例如关闭资源。为此使用 :meth:`teardown_request` 。

        这在应用程序和蓝图对象上都可用。当在应用程序上使用时，它会在每个请求后执行。当在
        蓝图上使用时，它会在蓝图处理的每个请求之后执行。要注册蓝图并在每个请求后执行，请
        使用 :meth:`.Blueprint.after_app_request` 。
        """
        self.after_request_funcs.setdefault(None, []).append(f)
        return f

    @setupmethod
    def teardown_request(self, f: T_teardown) -> T_teardown:
        """注册一个在弹出请求上下文时要调用的函数。通常，这发生在每个请求结束时，但可以
        在测试期间手动推送上下文。

        .. code-block:: python

            with app.test_request_context():
                ...

        当 ``with`` 块退出（或调用 ``ctx.pop()`` ）时，将在请求上下文变为非活动状态之前
        调用清理函数。

        当由于未处理的异常而调用清理函数时，它将传递一个错误对象。如果注册了
        :meth:`errorhandler` ，它将处理异常，而清理将不会收到它。

        清理函数必须避免引发异常。如果它们执行可能失败的代码，则必须用 ``try`` /
        ``except`` 块包围该代码并记录所有错误。

        清理函数的返回值将被忽略。

        这在应用程序和蓝图对象上都可用。当在应用程序上使用时，它会在每个请求后执行。当在
        蓝图上使用时，它会在蓝图处理的每个请求之后执行。要注册蓝图并在每个请求后执行，请
        使用 :meth:`.Blueprint.teardown_app_request` 。
        """
        self.teardown_request_funcs.setdefault(None, []).append(f)
        return f

    @setupmethod
    def context_processor(
        self,
        f: T_template_context_processor,
    ) -> T_template_context_processor:
        """注册模板上下文处理器函数。这些函数在渲染模板之前运行。返回的字典的键被添加为
        模板中可用的变量。

        这在应用程序和蓝图对象上都可用。当在应用程序上使用时，每个渲染的模板都会调用它。
        当在蓝图上使用时，会为从蓝图视图渲染的模板调用此方法。要注册蓝图并影响每个模板，
        请使用 :meth:`.Blueprint.app_context_processor` 。
        """
        self.template_context_processors[None].append(f)
        return f

    @setupmethod
    def url_value_preprocessor(
        self,
        f: T_url_value_preprocessor,
    ) -> T_url_value_preprocessor:
        """为应用程序中的所有视图函数注册一个 URL 值预处理器函数。这些函数将在
        :meth:`before_request` 函数之前调用。

        该函数可以在将匹配的 url 捕获的值传递到视图之前对其进行修改。例如，这可用于弹出
        公共语言代码值并将其放置在 ``g`` 中，而不是将其传递到每个视图。

        该函数传递端点名称和值字典。返回值被忽略。

        这在应用程序和蓝图对象上都可用。当在应用程序上使用时，每个请求都会调用它。当在蓝
        图上使用时，将针对蓝图处理的请求调用此方法。要注册蓝图并影响每个请求，请使用
        :meth:`.Blueprint.app_url_value_preprocessor` 。
        """
        self.url_value_preprocessors[None].append(f)
        return f

    @setupmethod
    def url_defaults(self, f: T_url_defaults) -> T_url_defaults:
        """URL 的回调函数默认用于应用程序的所有视图函数。  它是用端点和值调用的，并且应
        该更新就地传递的值。

        这在应用程序和蓝图对象上都可用。当在应用程序上使用时，每个请求都会调用它。当在蓝
        图上使用时，将针对蓝图处理的请求调用此方法。要注册蓝图并影响每个请求，请使用
        :meth:`.Blueprint.app_url_defaults` 。
        """
        self.url_default_functions[None].append(f)
        return f

    @setupmethod
    def errorhandler(
        self, code_or_exception: type[Exception] | int
    ) -> t.Callable[[T_error_handler], T_error_handler]:
        """注册一个函数来通过代码或异常类处理错误。

        用于注册给定错误代码的函数的装饰器。  示例::

            @app.errorhandler(404)
            def page_not_found(error):
                return 'This page does not exist', 404

        您还可以注册任意异常的处理程序::

            @app.errorhandler(DatabaseError)
            def special_exception_handler(error):
                return 'Database connection failed', 500

        这在应用程序和蓝图对象上都可用。当在应用程序上使用时，它可以处理每个请求的错误。
        当在蓝图上使用时，这可以处理蓝图处理的请求中的错误。要注册蓝图并影响每个请求，请
        使用 :meth:`.Blueprint.app_errorhandler` 。

        .. versionadded:: 0.7
            对于应用程序范围的错误处理程序，使用 :meth:`register_error_handler` 而不是直
            接修改 :attr:`error_handler_spec` 。

        .. versionadded:: 0.7
           现在还可以另外注册自定义异常类型，这些异常类型不一定必须是
           :class:`~werkzeug.exceptions.HTTPException` 类的子类。

        :param code_or_exception: 代码作为处理程序的整数，或任意异常
        """

        def decorator(f: T_error_handler) -> T_error_handler:
            self.register_error_handler(code_or_exception, f)
            return f

        return decorator

    @setupmethod
    def register_error_handler(
        self,
        code_or_exception: type[Exception] | int,
        f: ft.ErrorHandlerCallable,
    ) -> None:
        """替代错误将函数附加到 :meth:`errorhandler` 装饰器，对于非装饰器使用更直接。

        .. versionadded:: 0.7
        """
        exc_class, code = self._get_exc_class_and_code(code_or_exception)
        self.error_handler_spec[None][code][exc_class] = f

    @staticmethod
    def _get_exc_class_and_code(
        exc_class_or_code: type[Exception] | int,
    ) -> tuple[type[Exception], int | None]:
        """获取正在处理的异常类。对于 HTTP 状态代码或 ``HTTPException`` 子类，返回异常和
        状态代码。

        :param exc_class_or_code: 任何异常类，或整数形式的 HTTP 状态代码。
        """
        exc_class: type[Exception]

        if isinstance(exc_class_or_code, int):
            try:
                exc_class = default_exceptions[exc_class_or_code]
            except KeyError:
                raise ValueError(
                    f"'{exc_class_or_code}' is not a recognized HTTP"
                    " error code. Use a subclass of HTTPException with"
                    " that code instead."
                ) from None
        else:
            exc_class = exc_class_or_code

        if isinstance(exc_class, Exception):
            raise TypeError(
                f"{exc_class!r} is an instance, not a class. Handlers"
                " can only be registered for Exception classes or HTTP"
                " error codes."
            )

        if not issubclass(exc_class, Exception):
            raise ValueError(
                f"'{exc_class.__name__}' is not a subclass of Exception."
                " Handlers can only be registered for Exception classes"
                " or HTTP error codes."
            )

        if issubclass(exc_class, HTTPException):
            return exc_class, exc_class.code
        else:
            return exc_class, None


def _endpoint_from_view_func(view_func: ft.RouteCallable) -> str:
    """返回给定函数的默认端点的内部助手。  这始终是函数名称。"""
    assert view_func is not None, "expected view func if endpoint is not provided."
    return view_func.__name__


def _find_package_path(import_name: str) -> str:
    """查找包含包或模块的路径。"""
    root_mod_name, _, _ = import_name.partition(".")

    try:
        root_spec = importlib.util.find_spec(root_mod_name)

        if root_spec is None:
            raise ValueError("not found")
    except (ImportError, ValueError):
        # ImportError: 机器告诉我们它不存在 ValueError: - 模块名称无效 - 模块名称是
        # __main__ - 我们提出 `ValueError` 因为 `root_spec` 是 `None`
        return os.getcwd()

    if root_spec.submodule_search_locations:
        if root_spec.origin is None or root_spec.origin == "namespace":
            # 命名空间包
            package_spec = importlib.util.find_spec(import_name)

            if package_spec is not None and package_spec.submodule_search_locations:
                # 选择包含子模块的命名空间中的路径。
                package_path = pathlib.Path(
                    os.path.commonpath(package_spec.submodule_search_locations)
                )
                search_location = next(
                    location
                    for location in root_spec.submodule_search_locations
                    if package_path.is_relative_to(location)
                )
            else:
                # 选择第一条路径。
                search_location = root_spec.submodule_search_locations[0]

            return os.path.dirname(search_location)
        else:
            # 带有 __init__.py 的包
            return os.path.dirname(os.path.dirname(root_spec.origin))
    else:
        # 模块
        return os.path.dirname(root_spec.origin)  # type: ignore[type-var, return-value]


def find_package(import_name: str) -> tuple[str | None, str]:
    """查找安装包的前缀以及导入包的路径。

    前缀是包含标准目录层次结构（lib、bin 等）的目录。如果软件包未安装到系统 (
    :attr:`sys.prefix` ) 或 virtualenv ( ``site-packages`` )，则返回 ``None`` 。

    该路径是 :attr:`sys.path` 中包含要导入的包的条目。如果未安装该包，则假定该包是从当前
    工作目录导入的。
    """
    package_path = _find_package_path(import_name)
    py_prefix = os.path.abspath(sys.prefix)

    # 安装到系统中
    if pathlib.PurePath(package_path).is_relative_to(py_prefix):
        return py_prefix, package_path

    site_parent, site_folder = os.path.split(package_path)

    # 安装到 virtualenv
    if site_folder.lower() == "site-packages":
        parent, folder = os.path.split(site_parent)

        # Windows（前缀/lib/site-packages）
        if folder.lower() == "lib":
            return parent, package_path

        # Unix（前缀/lib/pythonX.Y/site-packages）
        if os.path.basename(parent).lower() == "lib":
            return os.path.dirname(parent), package_path

        # 其他东西（前缀/站点包）
        return site_parent, package_path

    # 未安装
    return None, package_path
