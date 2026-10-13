from __future__ import annotations

import collections.abc as cabc
import inspect
import os
import sys
import typing as t
import weakref
from datetime import timedelta
from functools import update_wrapper
from inspect import iscoroutinefunction
from itertools import chain
from types import TracebackType
from urllib.parse import quote as _url_quote
from urllib.parse import urlsplit

import click
from werkzeug.datastructures import Headers
from werkzeug.exceptions import BadRequestKeyError
from werkzeug.exceptions import HTTPException
from werkzeug.exceptions import InternalServerError
from werkzeug.routing import BuildError
from werkzeug.routing import MapAdapter
from werkzeug.routing import RequestRedirect
from werkzeug.routing import RoutingException
from werkzeug.routing import Rule
from werkzeug.serving import is_running_from_reloader
from werkzeug.wrappers import Response as BaseResponse
from werkzeug.wsgi import get_host

from . import cli
from . import typing as ft
from .ctx import AppContext
from .globals import _cv_app
from .globals import app_ctx
from .globals import g
from .globals import request
from .globals import session
from .helpers import _CollectErrors
from .helpers import get_debug_flag
from .helpers import get_flashed_messages
from .helpers import get_load_dotenv
from .helpers import send_from_directory
from .sansio.app import App
from .sessions import SecureCookieSessionInterface
from .sessions import SessionInterface
from .signals import appcontext_tearing_down
from .signals import got_request_exception
from .signals import request_finished
from .signals import request_started
from .signals import request_tearing_down
from .templating import Environment
from .wrappers import Request
from .wrappers import Response

if t.TYPE_CHECKING:  # pragma: no cover
    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIEnvironment

    from .testing import FlaskClient
    from .testing import FlaskCliRunner
    from .typing import HeadersValue

T_shell_context_processor = t.TypeVar(
    "T_shell_context_processor", bound=ft.ShellContextProcessorCallable
)
T_teardown = t.TypeVar("T_teardown", bound=ft.TeardownCallable)
T_template_filter = t.TypeVar("T_template_filter", bound=ft.TemplateFilterCallable)
T_template_global = t.TypeVar("T_template_global", bound=ft.TemplateGlobalCallable)
T_template_test = t.TypeVar("T_template_test", bound=ft.TemplateTestCallable)


def _make_timedelta(value: timedelta | int | None) -> timedelta | None:
    if value is None or isinstance(value, timedelta):
        return value

    return timedelta(seconds=value)


F = t.TypeVar("F", bound=t.Callable[..., t.Any])


# 其他方法可能会使用新的 ctx 参数调用重写的方法。删除它并使用剩余的参数调用该方法。
def remove_ctx(f: F) -> F:
    def wrapper(self: Flask, *args: t.Any, **kwargs: t.Any) -> t.Any:
        if args and isinstance(args[0], AppContext):
            args = args[1:]

        return f(self, *args, **kwargs)

    return update_wrapper(wrapper, f)  # type: ignore[return-value]


# 重写的方法可能会在没有新 ctx 参数的情况下调用 super().base_method 。将其添加到调用的参
# 数中。
def add_ctx(f: F) -> F:
    def wrapper(self: Flask, *args: t.Any, **kwargs: t.Any) -> t.Any:
        if not args:
            args = (app_ctx._get_current_object(),)
        elif not isinstance(args[0], AppContext):
            args = (app_ctx._get_current_object(), *args)

        return f(self, *args, **kwargs)

    return update_wrapper(wrapper, f)  # type: ignore[return-value]


class Flask(App):
    """Flask 对象实现 WSGI 应用程序并充当中心对象。  它传递应用程序的模块或包的名称。
    一旦创建，它将充当视图函数、URL 规则、模板配置等的中央注册表。

    包的名称用于解析包内部或模块所在文件夹中的资源，具体取决于包参数是否解析为实际的
    python 包（内部包含 :file:`__init__.py` 文件的文件夹）或标准模块（只是一个 ``.py``
    文件）。

    有关资源加载的更多信息，请参阅 :func:`open_resource` 。

    通常，您在主模块或包的 :file:`__init__.py` 文件中创建一个 :class:`Flask` 实例，如下
    所示::

        from flask import Flask
        app = Flask(__name__)

    .. admonition:: 关于第一个参数

        第一个参数的想法是让 Flask 了解什么属于您的应用程序。  该名称用于查找文件系统上
        的资源，可以由扩展使用来改进调试信息等等。

        所以你在那里提供什么很重要。  如果您使用单个模块， `__name__` 始终是正确的值。
        但是，如果您正在使用包，通常建议在此处硬编码包的名称。

        例如，如果您的应用程序是在 :file:`yourapplication/app.py` 中定义的，您应该使用以
        下两个版本之一创建它::

            app = Flask('yourapplication')
            app = Flask(__name__.split('.')[0])

        这是为什么呢？  由于查找资源的方式，该应用程序甚至可以使用 `__name__` 运行。  然
        而，这会让调试变得更加痛苦。  某些扩展可以根据应用程序的导入名称进行假设。  例如
        ，Flask-SQLAlchemy 扩展将在应用程序中查找在调试模式下触发 SQL 查询的代码。  如果
        导入名称设置不正确，调试信息就会丢失。  （例如，它只会在 `yourapplication.app`
        中拾取 SQL 查询，而不会在 `yourapplication.views.frontend` 中拾取）

    .. versionadded:: 0.7
       添加了 `static_url_path` 、 `static_folder` 和 `template_folder` 参数。

    .. versionadded:: 0.8
       添加了 `instance_path` 和 `instance_relative_config` 参数。

    .. versionadded:: 0.11
       添加了 `root_path` 参数。

    .. versionadded:: 1.0
       添加了 ``host_matching`` 和 ``static_host`` 参数。

    .. versionadded:: 1.0
       添加了 ``subdomain_matching`` 参数。现在需要手动启用子域匹配。设置
       :data:`SERVER_NAME` 不会隐式启用它。

    :param import_name: 应用程序包的名称
    :param static_url_path: 可用于为网络上的静态文件指定不同的路径。  默认为
        `static_folder` 文件夹的名称。
    :param static_folder: 包含在 ``static_url_path`` 处提供的静态文件的文件夹。相对于应
        用程序 ``root_path`` 或绝对路径。默认为 ``'static'`` 。
    :param static_host: 添加静态路由时要使用的主机。默认为 None。在配置了
        ``static_folder`` 的情况下使用 ``host_matching=True`` 时需要。
    :param host_matching: 设置 ``url_map.host_matching`` 属性。默认为 False。
    :param subdomain_matching: 匹配路由时考虑相对于 :data:`SERVER_NAME` 的子域。默认为
        False。
    :param template_folder: 包含应用程序应使用的模板的文件夹。  默认为应用程序根路径中的
        ``'templates'`` 文件夹。
    :param instance_path: 应用程序的替代实例路径。默认情况下，包或模块旁边的文件夹
        ``'instance'`` 被假定为实例路径。
    :param instance_relative_config: 如果设置为 ``True`` ，则用于加载配置的相对文件名被
        假定为相对于实例路径而不是应用程序根目录。
    :param root_path: 应用程序文件的根路径。仅当无法自动检测时才应手动设置，例如命名空间
        包。
    """

    default_config: cabc.Mapping[str, t.Any] = {
        "DEBUG": None,
        "TESTING": False,
        "PROPAGATE_EXCEPTIONS": None,
        "SECRET_KEY": None,
        "SECRET_KEY_FALLBACKS": None,
        "PERMANENT_SESSION_LIFETIME": timedelta(days=31),
        "USE_X_SENDFILE": False,
        "TRUSTED_HOSTS": None,
        "SERVER_NAME": None,
        "APPLICATION_ROOT": "/",
        "SESSION_COOKIE_NAME": "session",
        "SESSION_COOKIE_DOMAIN": None,
        "SESSION_COOKIE_PATH": None,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SECURE": False,
        "SESSION_COOKIE_PARTITIONED": False,
        "SESSION_COOKIE_SAMESITE": None,
        "SESSION_REFRESH_EACH_REQUEST": True,
        "MAX_CONTENT_LENGTH": None,
        "MAX_FORM_MEMORY_SIZE": 500_000,
        "MAX_FORM_PARTS": 1_000,
        "SEND_FILE_MAX_AGE_DEFAULT": None,
        "TRAP_BAD_REQUEST_ERRORS": None,
        "TRAP_HTTP_EXCEPTIONS": False,
        "EXPLAIN_TEMPLATE_LOADING": False,
        "PREFERRED_URL_SCHEME": "http",
        "TEMPLATES_AUTO_RELOAD": None,
        "MAX_COOKIE_SIZE": 4093,
        "PROVIDE_AUTOMATIC_OPTIONS": True,
    }

    #: 用于请求对象的类。  有关详细信息，请参阅 :class:`~flask.Request` 。
    request_class: type[Request] = Request

    #: 用于响应对象的类。  有关详细信息，请参阅 :class:`~flask.Response` 。
    response_class: type[Response] = Response

    #: 要使用的会话接口。  默认情况下，此处使用
    #: :class:`~flask.sessions.SecureCookieSessionInterface` 的实例。
    #:
    #: .. versionadded:: 0.8
    session_interface: SessionInterface = SecureCookieSessionInterface()

    def __init_subclass__(cls, **kwargs: t.Any) -> None:
        import warnings

        # 这些方法签名已更新为采用 ctx 参数。检测子类中仍具有旧签名的重写方法。显示弃用
        # 警告并换行以使用正确的参数进行调用。
        for method in (
            cls.handle_http_exception,
            cls.handle_user_exception,
            cls.handle_exception,
            cls.log_exception,
            cls.dispatch_request,
            cls.full_dispatch_request,
            cls.finalize_request,
            cls.make_default_options_response,
            cls.preprocess_request,
            cls.process_response,
            cls.do_teardown_request,
            cls.do_teardown_appcontext,
        ):
            base_method = getattr(Flask, method.__name__)

            if method is base_method:
                # 没有被覆盖
                continue

            # 获取第二个参数（第一个是self）
            iter_params = iter(inspect.signature(method).parameters.values())
            next(iter_params)
            param = next(iter_params, None)

            # 必须有第二个参数名为 ctx 或带注释的 AppContext
            if param is None or not (
                # 无注释，匹配名称
                (param.annotation is inspect.Parameter.empty and param.name == "ctx")
                or (
                    # 字符串注解，访问路径以AppContext结尾
                    isinstance(param.annotation, str)
                    and param.annotation.rpartition(".")[2] == "AppContext"
                )
                or (
                    # 类注释
                    inspect.isclass(param.annotation)
                    and issubclass(param.annotation, AppContext)
                )
            ):
                warnings.warn(
                    f"The '{method.__name__}' method now takes 'ctx: AppContext'"
                    " as the first parameter. The old signature is deprecated"
                    " and will not be supported in Flask 4.0.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                setattr(cls, method.__name__, remove_ctx(method))
                setattr(Flask, method.__name__, add_ctx(base_method))

    def __init__(
        self,
        import_name: str,
        static_url_path: str | None = None,
        static_folder: str | os.PathLike[str] | None = "static",
        static_host: str | None = None,
        host_matching: bool = False,
        subdomain_matching: bool = False,
        template_folder: str | os.PathLike[str] | None = "templates",
        instance_path: str | None = None,
        instance_relative_config: bool = False,
        root_path: str | None = None,
    ):
        super().__init__(
            import_name=import_name,
            static_url_path=static_url_path,
            static_folder=static_folder,
            static_host=static_host,
            host_matching=host_matching,
            subdomain_matching=subdomain_matching,
            template_folder=template_folder,
            instance_path=instance_path,
            instance_relative_config=instance_relative_config,
            root_path=root_path,
        )

        #: 用于为此对象注册 CLI 命令的 Click 命令组。一旦发现应用程序并注册了蓝图，就可
        #: 以通过 ``flask`` 命令使用这些命令。
        self.cli = cli.AppGroup()

        # 设置 Click 组的名称，以防有人想要将应用程序的命令添加到另一个 CLI 工具。
        self.cli.name = self.name

        # 如果已配置 static_folder，则使用提供的 static_url_path、static_host 和
        # static_folder 添加静态路由。请注意，我们这样做时没有检查 static_folder 是否存
        # 在。其一，它可能是在服务器运行时创建的（e.g.在开发期间）。此外，Google App
        # Engine 将静态文件存储在某处
        if self.has_static_folder:
            assert bool(static_host) == host_matching, (
                "Invalid static_host/host_matching combination"
            )
            # 使用weakref来避免在应用程序和视图函数之间创建引用循环（参见#3761）。
            self_ref = weakref.ref(self)
            self.add_url_rule(
                f"{self.static_url_path}/<path:filename>",
                endpoint="static",
                host=static_host,
                view_func=lambda **kw: self_ref().send_static_file(**kw),  # type: ignore
            )

    def get_send_file_max_age(self, filename: str | None) -> int | None:
        """如果未传递给定文件路径，则由 :func:`send_file` 使用来确定 ``max_age`` 缓存值
        。

        默认情况下，这会从 :data:`~flask.current_app` 的配置中返回
        :data:`SEND_FILE_MAX_AGE_DEFAULT` 。默认为 ``None`` ，它告诉浏览器使用条件请求而
        不是定时缓存，这通常是更好的选择。

        请注意，这是 Flask 类中相同方法的重复。

        .. versionchanged:: 2.0
            默认配置是 ``None`` 而不是 12 小时。

        .. versionadded:: 0.9
        """
        value = self.config["SEND_FILE_MAX_AGE_DEFAULT"]

        if value is None:
            return None

        if isinstance(value, timedelta):
            return int(value.total_seconds())

        return value  # type: ignore[no-any-return]

    def send_static_file(self, filename: str) -> Response:
        """用于提供来自 :attr:`static_folder` 的文件的视图函数。如果设置了
        :attr:`static_folder` ，则会在 :attr:`static_url_path` 处自动为此视图注册一条路
        由。

        请注意，这是 Flask 类中相同方法的重复。

        .. versionadded:: 0.5

        """
        if not self.has_static_folder:
            raise RuntimeError("'static_folder' must be set to serve static_files.")

        # send_file 只知道在应用程序上调用 get_send_file_max_age ，在此处调用它，因此它
        # 也适用于蓝图。
        max_age = self.get_send_file_max_age(filename)
        return send_from_directory(
            t.cast(str, self.static_folder), filename, max_age=max_age
        )

    def open_resource(
        self, resource: str, mode: str = "rb", encoding: str | None = None
    ) -> t.IO[t.AnyStr]:
        """打开相对于 :attr:`root_path` 的资源文件进行读取。

        例如，如果文件 ``schema.sql`` 位于定义 ``Flask`` 应用程序的文件 ``app.py`` 旁边
        ，则可以使用以下命令打开它：

        .. code-block:: python

            with app.open_resource("schema.sql") as f:
                conn.executescript(f.read())

        :param resource: 相对于 :attr:`root_path` 的资源路径。
        :param mode: 在此模式下打开文件。仅支持读取，有效值为 ``"r"`` （或 ``"rt"`` ）和
            ``"rb"`` 。
        :param encoding: 以文本模式打开时使用此编码打开文件。以二进制模式打开时会忽略这
            一点。

        .. versionchanged:: 3.1
            添加了 ``encoding`` 参数。
        """
        if mode not in {"r", "rt", "rb"}:
            raise ValueError("Resources can only be opened for reading.")

        path = os.path.join(self.root_path, resource)

        if mode == "rb":
            return open(path, mode)  # pyright: ignore

        return open(path, mode, encoding=encoding)

    def open_instance_resource(
        self, resource: str, mode: str = "rb", encoding: str | None = "utf-8"
    ) -> t.IO[t.AnyStr]:
        """打开与应用程序实例文件夹 :attr:`instance_path` 相关的资源文件。与
        :meth:`open_resource` 不同，实例文件夹中的文件可以打开进行写入。

        :param resource: 相对于 :attr:`instance_path` 的资源路径。
        :param mode: 在此模式下打开文件。
        :param encoding: 以文本模式打开时使用此编码打开文件。以二进制模式打开时会忽略这
            一点。

        .. versionchanged:: 3.1
            添加了 ``encoding`` 参数。
        """
        path = os.path.join(self.instance_path, resource)

        if "b" in mode:
            return open(path, mode)

        return open(path, mode, encoding=encoding)

    def create_jinja_environment(self) -> Environment:
        """基于 :attr:`jinja_options` 和应用程序的各种 Jinja 相关方法创建 Jinja 环境。此
        后更改 :attr:`jinja_options` 将无效。还将 Flask 相关的全局变量和过滤器添加到环境
        中。

        .. versionchanged:: 0.11
           ``Environment.auto_reload`` 根据 ``TEMPLATES_AUTO_RELOAD`` 配置选项设置。

        .. versionadded:: 0.5
        """
        options = dict(self.jinja_options)

        if "autoescape" not in options:
            options["autoescape"] = self.select_jinja_autoescape

        if "auto_reload" not in options:
            auto_reload = self.config["TEMPLATES_AUTO_RELOAD"]

            if auto_reload is None:
                auto_reload = self.debug

            options["auto_reload"] = auto_reload

        rv = self.jinja_environment(self, **options)
        rv.globals.update(
            url_for=self.url_for,
            get_flashed_messages=get_flashed_messages,
            config=self.config,
            # 出于效率原因，请求、会话和 g 通常与上下文处理器一起添加，但对于导入的模板
            # ，我们还需要其中的代理。
            request=request,
            session=session,
            g=g,
        )
        rv.policies["json.dumps_function"] = self.json.dumps
        return rv

    def create_url_adapter(self, request: Request | None) -> MapAdapter | None:
        """为给定请求创建 URL 适配器。 URL 适配器是在请求上下文尚未设置时创建的，因此请
        求是显式传递的。

        .. versionchanged:: 3.1
            如果设置了 :data:`SERVER_NAME` ，则对于 ``subdomain_matching`` 和
            ``host_matching`` 来说，它不会将请求限制为仅发送到该域。

        .. versionchanged:: 1.0
            :data:`SERVER_NAME` 不再隐式启用子域匹配。使用 :attr:`subdomain_matching` 代
            替。

        .. versionchanged:: 0.9
           当为应用程序上下文创建 URL 适配器时，可以在请求外部调用此方法。

        .. versionadded:: 0.6
        """
        if request is not None:
            if (trusted_hosts := self.config["TRUSTED_HOSTS"]) is not None:
                request.trusted_hosts = trusted_hosts

            # 检查 trusted_hosts 直到 bind_to_environ 完成。
            request.host = get_host(request.environ, request.trusted_hosts)  # pyright: ignore
            subdomain = None
            server_name = self.config["SERVER_NAME"]

            if self.url_map.host_matching:
                # 不要传递 SERVER_NAME，否则会使用它并忽略实际主机，这会破坏主机匹配。
                server_name = None
            elif not self.subdomain_matching:
                # Werkzeug 尚未实现子域匹配。在此之前，通过将当前子域强制为默认值或空字
                # 符串来禁用它。
                subdomain = self.url_map.default_subdomain or ""

            return self.url_map.bind_to_environ(
                request.environ, server_name=server_name, subdomain=subdomain
            )

        # 至少需要 SERVER_NAME 才能在请求之外匹配/构建。
        if self.config["SERVER_NAME"] is not None:
            return self.url_map.bind(
                self.config["SERVER_NAME"],
                script_name=self.config["APPLICATION_ROOT"],
                url_scheme=self.config["PREFERRED_URL_SCHEME"],
            )

        return None

    def raise_routing_exception(self, request: Request) -> t.NoReturn:
        """拦截路由异常并可能做其他事情。

        在调试模式下，拦截路由重定向，如果主体将被丢弃，则将其替换为错误。

        对于现代 Werkzeug ，这种情况不应该发生，因为它现在使用 308 状态告诉浏览器重新发
        送方法和正文。

        .. versionchanged:: 2.1
            不要拦截 307 和 308 重定向。

        ：元私有：：内部：
        """
        if (
            not self.debug
            or not isinstance(request.routing_exception, RequestRedirect)
            or request.routing_exception.code in {307, 308}
            or request.method in {"GET", "HEAD", "OPTIONS"}
        ):
            raise request.routing_exception  # type: ignore[misc]

        from .debughelpers import FormDataRoutingRedirect

        raise FormDataRoutingRedirect(request)

    def update_template_context(
        self, ctx: AppContext, context: dict[str, t.Any]
    ) -> None:
        """使用一些常用变量更新模板上下文。这会将 request、session、config 和 g 以及模板
        上下文处理器想要注入的所有内容注入到模板上下文中。  请注意，从 Flask 0.6 开始，
        如果上下文处理器决定返回具有相同键的值，则不会覆盖上下文中的原始值。

        :param context: 上下文作为字典，可以就地更新以添加额外的变量。
        """
        names: t.Iterable[str | None] = (None,)

        # 模板可以在请求上下文之外呈现。
        if ctx.has_request:
            names = chain(names, reversed(ctx.request.blueprints))

        # 传递给 render_template 的值优先。保留一份副本，以便在所有上下文功能之后重新应
        # 用。
        orig_ctx = context.copy()

        for name in names:
            if name in self.template_context_processors:
                for func in self.template_context_processors[name]:
                    context.update(self.ensure_sync(func)())

        context.update(orig_ctx)

    def make_shell_context(self) -> dict[str, t.Any]:
        """返回此应用程序的交互式 shell 的 shell 上下文。  这将运行所有已注册的 shell 上
        下文处理器。

        .. versionadded:: 0.11
        """
        rv = {"app": self, "g": g}
        for processor in self.shell_context_processors:
            rv.update(processor())
        return rv

    def run(
        self,
        host: str | None = None,
        port: int | None = None,
        debug: bool | None = None,
        load_dotenv: bool = True,
        **options: t.Any,
    ) -> None:
        """在本地开发服务器上运行应用程序。

        不要在生产环境中使用 ``run()`` 。它并不是为了满足生产服务器的安全和性能要求。相
        反，请参阅 :doc:`/deploying/index` 了解 WSGI 服务器建议。

        如果设置了 :attr:`debug` 标志，服务器将自动重新加载代码更改，并在发生异常时显示
        调试器。

        如果您想在调试模式下运行应用程序，但禁用交互式调试器上的代码执行，则可以传递
        ``use_evalex=False`` 作为参数。  这将使调试器的回溯屏幕保持活动状态，但禁用代码
        执行。

        不建议使用此功能进行自动重新加载的开发，因为此功能的支持很差。  相反，您应该使用
        :command:`flask` 命令行脚本的 ``run`` 支持。

        .. admonition:: 牢记在心

           Flask 将使用通用错误页面抑制任何服务器错误，除非处于调试模式。  因此，要仅启
           用交互式调试器而不重新加载代码，您必须使用 ``debug=True`` 和
           ``use_reloader=False`` 调用 :meth:`run` 。在不处于调试模式的情况下将
           ``use_debugger`` 设置为 ``True`` 不会捕获任何异常，因为不会捕获任何异常。

        :param host: 要监听的主机名。将其设置为 ``'0.0.0.0'`` 以使服务器也可以在外部使用
            。默认为 ``'127.0.0.1'`` 或 ``SERVER_NAME`` 配置变量中的主机（如果存在）。
        :param port: 网络服务器的端口。默认为 ``5000`` 或 ``SERVER_NAME`` 配置变量中定义
            的端口（如果存在）。
        :param debug: 如果给定，启用或禁用调试模式。请参阅 :attr:`debug` 。
        :param load_dotenv: 加载最近的 :file:`.env` 和 :file:`.flaskenv` 文件来设置环境
            变量。还将工作目录更改为包含找到的第一个文件的目录。
        :param options: 要转发到底层 Werkzeug 服务器的选项。请参阅
            :func:`werkzeug.serving.run_simple` 了解更多信息。

        .. versionchanged:: 1.0
            如果安装，python-dotenv 将用于从 :file:`.env` 和 :file:`.flaskenv` 文件加载
            环境变量。

            :envvar:`FLASK_DEBUG` 环境变量将覆盖 :attr:`debug` 。

            默认情况下启用线程模式。

        .. versionchanged:: 0.10
            现在从 ``SERVER_NAME`` 变量中选择默认端口。
        """
        # 忽略此调用，以便在使用 'flask run' 命令时它不会启动另一个服务器。
        if os.environ.get("FLASK_RUN_FROM_CLI") == "true":
            if not is_running_from_reloader():
                click.secho(
                    " * Ignoring a call to 'app.run()' that would block"
                    " the current 'flask' CLI command.\n"
                    "   Only call 'app.run()' in an 'if __name__ =="
                    ' "__main__"\' guard.',
                    fg="red",
                )

            return

        if get_load_dotenv(load_dotenv):
            cli.load_dotenv()

            # 如果设置，环境变量将覆盖现有值
            if "FLASK_DEBUG" in os.environ:
                self.debug = get_debug_flag()

        # 传递给方法的调试会覆盖所有其他源
        if debug is not None:
            self.debug = bool(debug)

        server_name = self.config.get("SERVER_NAME")
        sn_host = sn_port = None

        if server_name:
            server_url = urlsplit(f"//{server_name}")
            sn_host = server_url.hostname
            sn_port = server_url.port

        if not host:
            if sn_host:
                host = sn_host
            else:
                host = "127.0.0.1"

        if port or port == 0:
            port = int(port)
        elif sn_port is not None:
            port = sn_port
        else:
            port = 5000

        options.setdefault("use_reloader", self.debug)
        options.setdefault("use_debugger", self.debug)
        options.setdefault("threaded", True)

        cli.show_server_banner(self.debug, self.name)

        from werkzeug.serving import run_simple

        try:
            run_simple(host, port, self, **options)
        finally:
            # 如果开发服务器正常复位，则复位第一请求信息。  这使得无需重新加载程序即可从
            # 交互式 shell 中重新启动服务器。
            self._got_first_request = False

    def test_client(self, use_cookies: bool = True, **kwargs: t.Any) -> FlaskClient:
        """为此应用程序创建一个测试客户端。  有关单元测试的信息请前往 :doc:`/testing` 。

        请注意，如果您正在测试应用程序代码中的断言或异常，则必须设置
        ``app.testing = True`` 以便将异常传播到测试客户端。  否则，异常将由应用程序处理
        （对测试客户端不可见），并且 AssertionError 或其他异常的唯一指示将是对测试客户端
        的 500 状态代码响应。  请参阅 :attr:`testing` 属性。  例如::

            app.testing = True
            client = app.test_client()

        测试客户端可以在 ``with`` 块中使用，以推迟上下文的关闭，直到 ``with`` 块结束。
        如果您想访问上下文局部变量进行测试，这非常有用::

            with app.test_client() as c:
                rv = c.get('/?vodka=42')
                assert request.args['vodka'] == '42'

        此外，您可以传递可选的关键字参数，然后将其传递给应用程序的
        :attr:`test_client_class` 构造函数。例如::

            from flask.testing import FlaskClient

            class CustomClient(FlaskClient):
                def __init__(self, *args, **kwargs):
                    self._authentication = kwargs.pop("authentication")
                    super(CustomClient,self).__init__( *args, **kwargs)

            app.test_client_class = CustomClient
            client = app.test_client(authentication='Basic ....')

        有关详细信息，请参阅 :class:`~flask.testing.FlaskClient` 。

        .. versionchanged:: 0.4
           添加了对客户端 ``with`` 块使用的支持。

        .. versionadded:: 0.7
           添加了 `use_cookies` 参数，并能够通过设置 :attr:`test_client_class` 属性来覆
           盖要使用的客户端。

        .. versionchanged:: 0.11
           添加了 `**kwargs` 以支持将附加关键字参数传递给 :attr:`test_client_class` 的构
           造函数。
        """
        cls = self.test_client_class
        if cls is None:
            from .testing import FlaskClient as cls
        return cls(  # type: ignore
            self, self.response_class, use_cookies=use_cookies, **kwargs
        )

    def test_cli_runner(self, **kwargs: t.Any) -> FlaskCliRunner:
        """创建一个 CLI 运行器来测试 CLI 命令。请参阅 :ref:`testing-cli` 。

        返回 :attr:`test_cli_runner_class` 的实例，默认为
        :class:`~flask.testing.FlaskCliRunner` 。 Flask 应用程序对象作为第一个参数传递。

        .. versionadded:: 1.0
        """
        cls = self.test_cli_runner_class

        if cls is None:
            from .testing import FlaskCliRunner as cls

        return cls(self, **kwargs)  # type: ignore

    def handle_http_exception(
        self, ctx: AppContext, e: HTTPException
    ) -> HTTPException | ft.ResponseReturnValue:
        """处理 HTTP 异常。  默认情况下，这将调用注册的错误处理程序并返回异常作为响应。

        .. versionchanged:: 1.0.3
            ``RoutingException`` ，内部用于以下操作
             路由期间的斜杠重定向不会传递给错误处理程序。

        .. versionchanged:: 1.0
            异常由代码*和* MRO 查找，因此 ``HTTPException`` 子类可以使用基本
            ``HTTPException`` 的包罗万象的处理程序进行处理。

        .. versionadded:: 0.3
        """
        # 代理异常没有错误代码。  我们希望始终将那些未更改的错误作为错误返回
        if e.code is None:
            return e

        # RoutingExceptions 在内部用于触发路由操作，例如引发 RequestRedirect 的斜杠重定
        # 向。它们不是在用户代码中引发或处理的。
        if isinstance(e, RoutingException):
            return e

        handler = self._find_error_handler(e, ctx.request.blueprints)
        if handler is None:
            return e
        return self.ensure_sync(handler)(e)  # type: ignore[no-any-return]

    def handle_user_exception(
        self, ctx: AppContext, e: Exception
    ) -> HTTPException | ft.ResponseReturnValue:
        """每当发生需要处理的异常时，就会调用此方法。一个特殊情况是
        :class:`~werkzeug.exceptions.HTTPException` ，它被转发到
        :meth:`handle_http_exception` 方法。此函数将返回响应值或使用相同的回溯重新引发异
        常。

        .. versionchanged:: 1.0
            从请求数据（如 ``form`` ）引发的密钥错误在调试模式下显示错误密钥，而不是通用
            的错误请求消息。

        .. versionadded:: 0.7
        """
        if isinstance(e, BadRequestKeyError) and (
            self.debug or self.config["TRAP_BAD_REQUEST_ERRORS"]
        ):
            e.show_exception = True

        if isinstance(e, HTTPException) and not self.trap_http_exception(e):
            return self.handle_http_exception(ctx, e)

        handler = self._find_error_handler(e, ctx.request.blueprints)

        if handler is None:
            raise

        return self.ensure_sync(handler)(e)  # type: ignore[no-any-return]

    def handle_exception(self, ctx: AppContext, e: Exception) -> Response:
        """处理没有关联错误处理程序的异常，或者从错误处理程序引发的异常。这总是会导致
        500 ``InternalServerError`` 。

        始终发送 :data:`got_request_exception` 信号。

        如果 :data:`PROPAGATE_EXCEPTIONS` 是 ``True`` ，例如在调试模式下，错误将被重新引
        发，以便调试器可以显示它。否则，将记录原始异常，并返回
        :exc:`~werkzeug.exceptions.InternalServerError` 。

        如果为 ``InternalServerError`` 或 ``500`` 注册了错误处理程序，则将使用它。为了保
        持一致性，处理程序将始终接收 ``InternalServerError`` 。原始未处理的异常可用作
        ``e.original_exception`` 。

        .. versionchanged:: 1.1.0
            始终将 ``InternalServerError`` 实例传递给处理程序，将 ``original_exception``
            设置为未处理的错误。

        .. versionchanged:: 1.1.0
            当没有处理程序时，即使对于默认的 500 响应，也会完成 ``after_request`` 函数和
            其他终结。

        .. versionadded:: 0.3
        """
        exc_info = sys.exc_info()
        got_request_exception.send(self, _async_wrapper=self.ensure_sync, exception=e)
        propagate = self.config["PROPAGATE_EXCEPTIONS"]

        if propagate is None:
            propagate = self.testing or self.debug

        if propagate:
            # 如果使用活动异常调用，则重新引发，否则引发传入的异常。
            if exc_info[1] is e:
                raise

            raise e

        self.log_exception(ctx, exc_info)
        server_error: InternalServerError | ft.ResponseReturnValue
        server_error = InternalServerError(original_exception=e)
        handler = self._find_error_handler(server_error, ctx.request.blueprints)

        if handler is not None:
            server_error = self.ensure_sync(handler)(server_error)

        return self.finalize_request(ctx, server_error, from_error_handler=True)

    def log_exception(
        self,
        ctx: AppContext,
        exc_info: tuple[type, BaseException, TracebackType] | tuple[None, None, None],
    ) -> None:
        """记录异常。  如果禁用调试且在调用处理程序之前，则由 :meth:`handle_exception`
        调用此函数。默认实现将异常记录为 :attr:`logger` 上的错误。

        .. versionadded:: 0.8
        """
        self.logger.error(
            f"Exception on {ctx.request.path} [{ctx.request.method}]", exc_info=exc_info
        )

    def dispatch_request(self, ctx: AppContext) -> ft.ResponseReturnValue:
        """是否进行请求调度。  匹配 URL 并返回视图或错误处理程序的返回值。  这不一定是响
        应对象。  为了将返回值转换为正确的响应对象，请调用 :func:`make_response` 。

        .. versionchanged:: 0.7
           这不再进行异常处理，此代码已移至新的 :meth:`full_dispatch_request` 。
        """
        req = ctx.request

        if req.routing_exception is not None:
            self.raise_routing_exception(req)
        rule: Rule = req.url_rule  # type: ignore[assignment]
        # 如果我们为此 URL 提供自动选项，并且请求通过 OPTIONS 方法发出，则自动回复
        if (
            getattr(rule, "provide_automatic_options", False)
            and req.method == "OPTIONS"
        ):
            return self.make_default_options_response(ctx)
        # 否则分派到该端点的处理程序
        view_args: dict[str, t.Any] = req.view_args  # type: ignore[assignment]
        return self.ensure_sync(self.view_functions[rule.endpoint])(**view_args)  # type: ignore[no-any-return]

    def full_dispatch_request(self, ctx: AppContext) -> Response:
        """分派请求，并在此之上执行请求预处理和后处理以及 HTTP 异常捕获和错误处理。

        .. versionadded:: 0.7
        """
        if not self._got_first_request and self.should_ignore_error is not None:
            import warnings

            warnings.warn(
                "The 'should_ignore_error' method is deprecated and will"
                " be removed in Flask 3.3. Handle errors as needed in"
                " teardown handlers instead.",
                DeprecationWarning,
                stacklevel=1,
            )

        self._got_first_request = True

        try:
            request_started.send(self, _async_wrapper=self.ensure_sync)
            rv = self.preprocess_request(ctx)
            if rv is None:
                rv = self.dispatch_request(ctx)
        except Exception as e:
            rv = self.handle_user_exception(ctx, e)
        return self.finalize_request(ctx, rv)

    def finalize_request(
        self,
        ctx: AppContext,
        rv: ft.ResponseReturnValue | HTTPException,
        from_error_handler: bool = False,
    ) -> Response:
        """给定视图函数的返回值，通过将请求转换为响应并调用后处理函数来完成请求。  正常
        请求分派和错误处理程序都会调用此方法。

        因为这意味着它可能会因失败而被调用，所以可以使用特殊的安全模式，可以使用
        `from_error_handler` 标志启用该模式。  如果启用，响应处理中的失败将被记录下来，
        否则将被忽略。

        ：内部：
        """
        response = self.make_response(rv)
        try:
            response = self.process_response(ctx, response)
            request_finished.send(
                self, _async_wrapper=self.ensure_sync, response=response
            )
        except Exception:
            if not from_error_handler:
                raise
            self.logger.exception(
                "Request finalizing failed with an error while handling an error"
            )
        return response

    def make_default_options_response(self, ctx: AppContext) -> Response:
        """调用此方法来创建默认的 ``OPTIONS`` 响应。这可以通过子类化来更改 ``OPTIONS``
        响应的默认行为。

        .. versionadded:: 0.7
        """
        methods = ctx.url_adapter.allowed_methods()  # type: ignore[union-attr]
        rv = self.response_class()
        rv.allow.update(methods)
        return rv

    def ensure_sync(self, func: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
        """确保该函数对于 WSGI 工作线程是同步的。普通 ``def`` 函数按原样返回。
        ``async def`` 函数被包装以运行并等待响应。

        重写此方法以更改应用程序运行异步视图的方式。

        .. versionadded:: 2.0
        """
        if iscoroutinefunction(func):
            return self.async_to_sync(func)

        return func

    def async_to_sync(
        self, func: t.Callable[..., t.Coroutine[t.Any, t.Any, t.Any]]
    ) -> t.Callable[..., t.Any]:
        """返回将运行协程函数的同步函数。

        .. code-block:: python

            result = app.async_to_sync(func)(*args, **kwargs)

        重写此方法以更改应用程序将异步代码转换为可同步调用的方式。

        .. versionadded:: 2.0
        """
        try:
            from asgiref.sync import async_to_sync as asgiref_async_to_sync
        except ImportError:
            raise RuntimeError(
                "Install Flask with the 'async' extra in order to use async views."
            ) from None

        return asgiref_async_to_sync(func)

    def url_for(
        self,
        /,
        endpoint: str,
        *,
        _anchor: str | None = None,
        _method: str | None = None,
        _scheme: str | None = None,
        _external: bool | None = None,
        **values: t.Any,
    ) -> str:
        """使用给定值生成给定端点的 URL 。

        这是由 :func:`flask.url_for` 调用的，也可以直接调用。

        *endpoint* 是 URL 规则的名称，通常添加 :meth:`@app.route() <route>` ，并且通常与
        视图函数同名。 :class:`~flask.Blueprint` 中定义的路由将在端点前面添加由 ``.`` 分
        隔的蓝图名称。

        在某些情况下，例如电子邮件，您希望 URL 包含方案和域，例如
        ``https://example.com/hello`` 。当不在活动请求中时，默认情况下 URL 将是外部的，
        但这需要设置 :data:`SERVER_NAME` 以便 Flask 知道要使用哪个域。
        :data:`APPLICATION_ROOT` 和 :data:`PREFERRED_URL_SCHEME` 也应根据需要进行配置。
        此配置仅在不在活动请求中时使用。

        可以使用 :meth:`url_defaults` 修饰函数，以在构建 URL 之前修改关键字参数。

        如果由于某种原因（例如未知端点或不正确的值）构建失败，则会调用应用程序的
        :meth:`handle_url_build_error` 方法。如果返回一个字符串，则返回该字符串，否则引
        发 :exc:`~werkzeug.routing.BuildError` 。

        :param endpoint: 与要生成的 URL 关联的端点名称。如果以 ``.`` 开头，则将使用当前
            的蓝图名称（如果有）。
        :param _anchor: 如果给出，请将其作为 ``#anchor`` 附加到 URL 中。
        :param _method: 如果给定，则为端点生成与此方法关联的 URL。
        :param _scheme: 如果给定，URL 将具有此方案（如果它是外部的）。
        :param _external: 如果给定，则首选 URL 为内部 (False) 或要求它为外部 (True)。外
            部 URL 包括方案和域。当不在活动请求中时，URL 默认是外部的。
        :param values: 用于 URL 规则的变量部分的值。未知键作为查询字符串参数附加，例如
            ``?a=b&c=d`` 。

        .. versionadded:: 2.2
            从 ``flask.url_for`` 移出，它调用此方法。
        """
        if (ctx := _cv_app.get(None)) is not None and ctx.has_request:
            url_adapter = ctx.url_adapter
            blueprint_name = ctx.request.blueprint

            # 如果端点以 "." 开头并且请求与蓝图匹配，则端点是相对于蓝图的。
            if endpoint[:1] == ".":
                if blueprint_name is not None:
                    endpoint = f"{blueprint_name}{endpoint}"
                else:
                    endpoint = endpoint[1:]

            # 在请求时，默认生成不带方案和域的 URL ，除非给出方案。
            if _external is None:
                _external = _scheme is not None
        else:
            # 如果由 helpers.url_for 调用，则应用程序上下文处于活动状态，请使用其
            # url_adapter。否则，直接调用app.url_for，构建适配器。
            if ctx is not None:
                url_adapter = ctx.url_adapter
            else:
                url_adapter = self.create_url_adapter(None)

            if url_adapter is None:
                raise RuntimeError(
                    "Unable to build URLs outside an active request"
                    " without 'SERVER_NAME' configured. Also configure"
                    " 'APPLICATION_ROOT' and 'PREFERRED_URL_SCHEME' as"
                    " needed."
                )

            # 当外部请求时，默认生成带有方案和域的 URL 。
            if _external is None:
                _external = True

        # 当 _external=False 时设置 _scheme 是错误的，以避免意外出现不安全的 URL。
        if _scheme is not None and not _external:
            raise ValueError("When specifying '_scheme', '_external' must be True.")

        self.inject_url_defaults(endpoint, values)

        try:
            rv = url_adapter.build(  # type: ignore[union-attr]
                endpoint,
                values,
                method=_method,
                url_scheme=_scheme,
                force_external=_external,
            )
        except BuildError as error:
            values.update(
                _anchor=_anchor, _method=_method, _scheme=_scheme, _external=_external
            )
            return self.handle_url_build_error(error, endpoint, values)

        if _anchor is not None:
            _anchor = _url_quote(_anchor, safe="%!#$&'()*+,/:;=?@")
            rv = f"{rv}#{_anchor}"

        return rv

    def make_response(self, rv: ft.ResponseReturnValue) -> Response:
        """将视图函数的返回值转换为 :attr:`response_class` 的实例。

        :param rv: 视图函数的返回值。视图函数必须返回响应。不允许返回 ``None`` 或视图结
            束而不返回。 ``view_rv`` 允许使用以下类型：

            ``str``
                使用编码为 UTF-8 的字符串作为正文创建响应对象。

            ``bytes``
                使用字节作为主体创建响应对象。

            ``dict``
                转换为 JSON 响应。

            ``list``
                转换为 JSON 响应。

            ``generator`` 或 ``iterator``
                返回 ``str`` 或 ``bytes`` 的生成器作为响应进行流式传输。

            ``tuple``
                ``(body, status, headers)`` 、 ``(body, status)`` 或 ``(body, headers)``
                ，其中 ``body`` 是此处允许的任何其他类型， ``status`` 是字符串或整数，
                ``headers`` 是字典或 ``(key, value)`` 元组列表。如果 ``body`` 是
                :attr:`response_class` 实例，则 ``status`` 会覆盖现有值，并扩展
                ``headers`` 。

            :attr:`response_class`
                对象原样返回。

            其他 :class:`~werkzeug.wrappers.Response` 类
                该对象被强制为 :attr:`response_class` 。

            :func:`callable`
                该函数被称为 WSGI 应用程序。结果用于创建响应对象。

        .. versionchanged:: 2.2
            生成器将转换为流响应。列表将转换为 JSON 响应。

        .. versionchanged:: 1.1
            字典将转换为 JSON 响应。

        .. versionchanged:: 0.9
           以前，元组被解释为响应对象的参数。
        """

        status: int | None = None
        headers: HeadersValue | None = None

        # 解包元组返回
        if isinstance(rv, tuple):
            len_rv = len(rv)

            # 3元组直接解包
            if len_rv == 3:
                rv, status, headers = rv  # type: ignore[misc]
            # 确定 2 元组是否有状态或标头
            elif len_rv == 2:
                if isinstance(rv[1], (Headers, dict, tuple, list)):
                    rv, headers = rv  # pyright: ignore
                else:
                    rv, status = rv  # type: ignore[assignment,misc]
            # 不允许使用其他大小的元组
            else:
                raise TypeError(
                    "The view function did not return a valid response tuple."
                    " The tuple must have the form (body, status, headers),"
                    " (body, status), or (body, headers)."
                )

        # 主体不能是 None
        if rv is None:
            raise TypeError(
                f"The view function for {request.endpoint!r} did not"
                " return a valid response. The function either returned"
                " None or ended without a return statement."
            )

        # 确保主体是响应类的实例
        if not isinstance(rv, self.response_class):
            if isinstance(rv, (str, bytes, bytearray)) or isinstance(rv, cabc.Iterator):
                # 让响应类设置状态和标头，而不是等待手动完成，以便该类可以处理任何特殊逻
                # 辑
                rv = self.response_class(
                    rv,  # pyright: ignore
                    status=status,
                    headers=headers,  # type: ignore[arg-type]
                )
                status = headers = None
            elif isinstance(rv, (dict, list)):
                rv = self.json.response(rv)
            elif isinstance(rv, BaseResponse) or callable(rv):
                # 评估 WSGI 可调用，或将不同的响应类强制为正确的类型
                try:
                    rv = self.response_class.force_type(
                        rv,  # type: ignore[arg-type]
                        request.environ,
                    )
                except TypeError as e:
                    raise TypeError(
                        f"{e}\nThe view function did not return a valid"
                        " response. The return type must be a string,"
                        " dict, list, tuple with headers or status,"
                        " Response instance, or WSGI callable, but it"
                        f" was a {type(rv).__name__}."
                    ).with_traceback(sys.exc_info()[2]) from None
            else:
                raise TypeError(
                    "The view function did not return a valid"
                    " response. The return type must be a string,"
                    " dict, list, tuple with headers or status,"
                    " Response instance, or WSGI callable, but it was a"
                    f" {type(rv).__name__}."
                )

        rv = t.cast(Response, rv)
        # 更喜欢该状态（如果已提供）
        if status is not None:
            if isinstance(status, (str, bytes, bytearray)):
                rv.status = status
            else:
                rv.status_code = status

        # 使用提供的标头扩展现有标头
        if headers:
            rv.headers.update(headers)

        return rv

    def preprocess_request(self, ctx: AppContext) -> ft.ResponseReturnValue | None:
        """在发送请求之前调用。调用向应用程序和当前蓝图（如果有）注册的
        :attr:`url_value_preprocessors` 。然后调用向应用程序和蓝图注册的
        :attr:`before_request_funcs` 。

        如果任何 :meth:`before_request` 处理程序返回非 None 值，则该值将被视为视图的返回
        值，并停止进一步的请求处理。
        """
        req = ctx.request
        names = (None, *reversed(req.blueprints))

        for name in names:
            if name in self.url_value_preprocessors:
                for url_func in self.url_value_preprocessors[name]:
                    url_func(req.endpoint, req.view_args)

        for name in names:
            if name in self.before_request_funcs:
                for before_func in self.before_request_funcs[name]:
                    rv = self.ensure_sync(before_func)()

                    if rv is not None:
                        return rv  # type: ignore[no-any-return]

        return None

    def process_response(self, ctx: AppContext, response: Response) -> Response:
        """可以被覆盖，以便在将响应对象发送到 WSGI 服务器之前修改响应对象。  默认情况下
        ，这将调用所有 :meth:`after_request` 修饰函数。

        .. versionchanged:: 0.5
           从 Flask 0.5 开始，请求执行后注册的函数将以注册的相反顺序调用。

        :param response: 一个 :attr:`response_class` 对象。
        :return: 新的响应对象或相同的对象必须是 :attr:`response_class` 的实例。
        """
        for func in ctx._after_request_functions:
            response = self.ensure_sync(func)(response)

        for name in chain(ctx.request.blueprints, (None,)):
            if name in self.after_request_funcs:
                for func in reversed(self.after_request_funcs[name]):
                    response = self.ensure_sync(func)(response)

        if not self.session_interface.is_null_session(ctx._get_session()):
            self.session_interface.save_session(self, ctx._get_session(), response)

        return response

    def do_teardown_request(
        self, ctx: AppContext, exc: BaseException | None = None
    ) -> None:
        """在分派请求并最终确定响应之后、弹出请求上下文之前调用。由
        :meth:`.AppContext.pop` 调用。

        如果蓝图处理了请求，这将调用所有用 :meth:`teardown_request` 和
        :meth:`Blueprint.teardown_request` 修饰的函数。最后，发送
        :data:`request_tearing_down` 信号。

        :param exc: 分派请求时引发未处理的异常。传递给每个清理函数。

        .. versionchanged:: 3.2
            所有回调都会被调用，而不是在第一个错误时停止。

        .. versionchanged:: 0.9
            添加了 ``exc`` 参数。
        """
        collect_errors = _CollectErrors()

        for name in chain(ctx.request.blueprints, (None,)):
            if name in self.teardown_request_funcs:
                for func in reversed(self.teardown_request_funcs[name]):
                    with collect_errors:
                        self.ensure_sync(func)(exc)

        with collect_errors:
            request_tearing_down.send(self, _async_wrapper=self.ensure_sync, exc=exc)

        collect_errors.raise_any("Errors during request teardown")

    def do_teardown_appcontext(
        self, ctx: AppContext, exc: BaseException | None = None
    ) -> None:
        """在弹出应用程序上下文之前调用。由 :meth:`.AppContext.pop` 调用。

        这将调用所有用 :meth:`teardown_appcontext` 修饰的函数。然后发送
        :data:`appcontext_tearing_down` 信号。

        :param exc: 上下文处于活动状态时引发未处理的异常。传递给每个清理函数。

        .. versionchanged:: 3.2
            所有回调都会被调用，而不是在第一个错误时停止。

        .. versionadded:: 0.9
        """
        collect_errors = _CollectErrors()

        for func in reversed(self.teardown_appcontext_funcs):
            with collect_errors:
                self.ensure_sync(func)(exc)

        with collect_errors:
            appcontext_tearing_down.send(self, _async_wrapper=self.ensure_sync, exc=exc)

        collect_errors.raise_any("Errors during app teardown")

    def app_context(self) -> AppContext:
        """创建一个 :class:`.AppContext` 。当上下文被推送时， :data:`.current_app` 和
        :data:`.g` 变得可用。

        处理每个请求以及运行任何 ``flask`` CLI 命令时，会自动推送上下文。使用它作为
        ``with`` 块来手动将上下文推送到这些情况之外，例如在设置或测试期间。

        .. code-block:: python

            with app.app_context():
                init_db()

        请参阅 :doc:`/appcontext` 。

        .. versionadded:: 0.9
        """
        return AppContext(self)

    def request_context(self, environ: WSGIEnvironment) -> AppContext:
        """使用表示给定 WSGI 环境的请求信息创建 :class:`.AppContext` 。处理每个请求时会
        自动推送上下文。当上下文被推送时， :data:`.request` 、 :data:`.session` 、
        :data:`g` 和 :data:`.current_app` 变得可用。

        此方法不应在您自己的代码中使用。创建一个有效的 WSGI 环境并不简单。使用
        :meth:`test_request_context` 正确创建 WSGI 环境并请求上下文。

        请参阅 :doc:`/appcontext` 。

        :param environ: WSGI 环境。
        """
        return AppContext.from_environ(self, environ)

    def test_request_context(self, *args: t.Any, **kwargs: t.Any) -> AppContext:
        """使用根据给定参数创建的请求信息创建 :class:`.AppContext` 。当上下文被推送时，
        :data:`.request` 、 :data:`.session` 、 :data:`g` 和 :data:`.current_app` 变得可
        用。

        这在测试运行使用请求数据而不分派完整请求的函数时非常有用。使用它作为 ``with`` 块
        来推送上下文。

        .. code-block:: python

            with app.test_request_context(...):
                generate_report()

        请参阅 :doc:`/appcontext` 。

        采用与 Werkzeug 的 :class:`~werkzeug.test.EnvironBuilder` 相同的参数，并使用应用
        程序的一些默认值。有关大多数可用参数，请参阅链接的 Werkzeug 文档。此处列出了
        Flask 特定的行为。

        :param path: 正在请求 URL 路径。
        :param base_url: 提供应用程序的基础 URL，与 ``path`` 相关。如果未给出，则从
            :data:`PREFERRED_URL_SCHEME` 、 ``subdomain`` 、 :data:`SERVER_NAME` 和
            :data:`APPLICATION_ROOT` 构建。
        :param subdomain: 要添加到 :data:`SERVER_NAME` 前面的子域名。
        :param url_scheme: 使用方案代替 :data:`PREFERRED_URL_SCHEME` 。
        :param data: 请求正文文本或字节，或表单数据的字典。
        :param json: 如果给定，则将其序列化为 JSON 并作为 ``data`` 传递。还将
            ``content_type`` 默认为 ``application/json`` 。
        :param args: 其他位置参数传递给 :class:`~werkzeug.test.EnvironBuilder` 。
        :param kwargs: 其他关键字参数传递给 :class:`~werkzeug.test.EnvironBuilder` 。
        """
        from .testing import EnvironBuilder

        builder = EnvironBuilder(self, *args, **kwargs)

        try:
            environ = builder.get_environ()
        finally:
            builder.close()

        return self.request_context(environ)

    def wsgi_app(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> cabc.Iterable[bytes]:
        """实际的 WSGI 应用程序。这没有在 :meth:`__call__` 中实现，因此可以应用中间件而
        不会丢失对应用程序对象的引用。而不是这样做::

            app = MyMiddleware(app)

        这样做是一个更好的主意::

            app.wsgi_app = MyMiddleware(app.wsgi_app)

        然后，您仍然拥有原始应用程序对象，并且可以继续调用它的方法。

        .. versionchanged:: 0.7
            即使发生未处理的错误，也会调用请求和应用程序上下文的清理事件。根据调度期间发
            生错误的时间，可能不会调用其他事件。

        :param environ: WSGI 环境。
        :param start_response: 可调用接受状态代码、标头列表和用于启动响应的可选异常上下
            文。
        """
        ctx = self.request_context(environ)
        error: BaseException | None = None
        try:
            try:
                ctx.push()
                response = self.full_dispatch_request(ctx)
            except Exception as e:
                error = e
                response = self.handle_exception(ctx, e)
            except:
                error = sys.exc_info()[1]
                raise
            return response(environ, start_response)
        finally:
            if "werkzeug.debug.preserve_context" in environ:
                environ["werkzeug.debug.preserve_context"](ctx)

            if (
                error is not None
                and self.should_ignore_error is not None
                and self.should_ignore_error(error)
            ):
                error = None

            ctx.pop(error)

    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> cabc.Iterable[bytes]:
        """WSGI 服务器将 Flask 应用程序对象调用为 WSGI 应用程序。这会调用
        :meth:`wsgi_app` ，它可以被包装以应用中间件。
        """
        return self.wsgi_app(environ, start_response)
