from __future__ import annotations

import collections.abc as cabc
import logging
import os
import sys
import typing as t
from datetime import timedelta
from itertools import chain

from werkzeug.exceptions import Aborter
from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import BadRequestKeyError
from werkzeug.routing import BuildError
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.sansio.response import Response
from werkzeug.utils import cached_property
from werkzeug.utils import redirect as _wz_redirect

from .. import typing as ft
from ..config import Config
from ..config import ConfigAttribute
from ..ctx import _AppCtxGlobals
from ..helpers import _split_blueprint_path
from ..helpers import get_debug_flag
from ..json.provider import DefaultJSONProvider
from ..json.provider import JSONProvider
from ..logging import create_logger
from ..templating import DispatchingJinjaLoader
from ..templating import Environment
from .scaffold import _endpoint_from_view_func
from .scaffold import find_package
from .scaffold import Scaffold
from .scaffold import setupmethod

if t.TYPE_CHECKING:  # pragma: no cover
    from werkzeug.wrappers import Response as BaseResponse

    from ..testing import FlaskClient
    from ..testing import FlaskCliRunner
    from .blueprints import Blueprint

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


class App(Scaffold):
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

    #: 分配给 :attr:`aborter` 的对象的类，由 :meth:`create_aborter` 创建。该对象由
    #: :func:`flask.abort` 调用以引发 HTTP 错误，也可以直接调用。
    #:
    #: 默认为 :class:`werkzeug.exceptions.Aborter` 。
    #:
    #: .. versionadded:: 2.2
    aborter_class = Aborter

    #: 用于 Jinja 环境的类。
    #:
    #: .. versionadded:: 0.11
    jinja_environment = Environment

    #: 用于 :data:`~flask.g` 实例的类。
    #:
    #: 自定义类的示例用例：
    #:
    #: 1. 在 flask.g 上存储任意属性。
    #: 2. 为每个请求的惰性数据库连接器添加一个属性。
    #: 3. 对于意外属性，返回 None 而不是 AttributeError。
    #: 4. 如果设置了意外的属性，即 "controlled" flask.g，则引发异常。
    #:
    #: .. versionadded:: 0.10
    #:     从“ `request_globals_class` ”重命名。
    app_ctx_globals_class = _AppCtxGlobals

    #: 用于此应用程序的 ``config`` 属性的类。默认为 :class:`~flask.Config` 。
    #:
    #: 自定义类的示例用例：
    #:
    #: 1. 某些配置选项的默认值。
    #: 2. 除了键之外，还可以通过属性访问配置值。
    #:
    #: .. versionadded:: 0.11
    config_class = Config

    #: 测试标志。  将其设置为 ``True`` 以启用 Flask 扩展的测试模式（将来可能还包括 Flask
    #: 本身）。例如，这可能会激活具有额外运行时成本的测试助手，默认情况下不应启用该成本
    #: 。
    #:
    #: 如果启用此功能并且 PROPAGATE_EXCEPTIONS 未更改默认值，则它会隐式启用。
    #:
    #: 还可以使用 ``TESTING`` 配置键从配置中配置此属性。  默认为 ``False`` 。
    testing = ConfigAttribute[bool]("TESTING")

    #: 如果设置了密钥，加密组件可以使用它来签署 cookie 和其他内容。例如，当您想要使用安
    #: 全 cookie 时，请将其设置为复杂的随机值。
    #:
    #: 还可以使用 :data:`SECRET_KEY` 配置键从配置中配置此属性。默认为 ``None`` 。
    secret_key = ConfigAttribute[str | bytes | None]("SECRET_KEY")

    #: :class:`~datetime.timedelta` 用于设置永久会话的到期日期。  默认值为 31 天，这使得
    #: 永久会话可以存活大约一个月。
    #:
    #: 还可以使用 ``PERMANENT_SESSION_LIFETIME`` 配置键从配置中配置此属性。  默认为
    #: ``timedelta(days=31)``
    permanent_session_lifetime = ConfigAttribute[timedelta](
        "PERMANENT_SESSION_LIFETIME",
        get_converter=_make_timedelta,  # type: ignore[arg-type]
    )

    json_provider_class: type[JSONProvider] = DefaultJSONProvider
    """:class:`~flask.json.provider.JSONProvider` 的子类。创建应用程序时会创建一个实例并
    将其分配给 :attr:`app.json` 。

    默认值 :class:`~flask.json.provider.DefaultJSONProvider` 使用 Python 的内置
    :mod:`json` 库。不同的提供者可以使用不同的 JSON 库。

    .. versionadded:: 2.2
    """

    #: 传递到 :meth:`create_jinja_environment` 中的 Jinja 环境的选项。创建环境后更改这些
    #: 选项（访问 :attr:`jinja_env` ）将无效。
    #:
    #: .. versionchanged:: 1.1.0
    #:     这是 ``dict`` 而不是 ``ImmutableDict`` ，以允许更轻松的配置。
    #:
    jinja_options: dict[str, t.Any] = {}

    #: 用于创建 URL 规则的规则对象。  这是由 :meth:`add_url_rule` 使用的。  默认为
    #: :class:`werkzeug.routing.Rule` 。
    #:
    #: .. versionadded:: 0.7
    url_rule_class = Rule

    #: 用于存储 URL 规则和路由配置参数的映射对象。默认为 :class:`werkzeug.routing.Map`
    #: 。
    #:
    #: .. versionadded:: 1.1.0
    url_map_class = Map

    #: :meth:`test_client` 方法创建此测试客户端类的实例。默认为
    #: :class:`~flask.testing.FlaskClient` 。
    #:
    #: .. versionadded:: 0.7
    test_client_class: type[FlaskClient] | None = None

    #: :class:`~click.testing.CliRunner` 子类，默认情况下
    #: :class:`~flask.testing.FlaskCliRunner` 由 :meth:`test_cli_runner` 使用。它的
    #: ``__init__`` 方法应该采用 Flask 应用程序对象作为第一个参数。
    #:
    #: .. versionadded:: 1.0
    test_cli_runner_class: type[FlaskCliRunner] | None = None

    default_config: cabc.Mapping[str, t.Any]
    response_class: type[Response]

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
    ) -> None:
        super().__init__(
            import_name=import_name,
            static_folder=static_folder,
            static_url_path=static_url_path,
            template_folder=template_folder,
            root_path=root_path,
        )

        if instance_path is None:
            instance_path = self.auto_find_instance_path()
        elif not os.path.isabs(instance_path):
            raise ValueError(
                "If an instance path is provided it must be absolute."
                " A relative path was given instead."
            )

        #: 保存实例文件夹的路径。
        #:
        #: .. versionadded:: 0.8
        self.instance_path = instance_path

        #: 配置字典为 :class:`Config` 。  其行为与常规字典完全相同，但支持从文件加载配置
        #: 的其他方法。
        self.config = self.make_config(instance_relative_config)

        #: 由 :meth:`make_aborter` 创建的 :attr:`aborter_class` 实例。这由
        #: :func:`flask.abort` 调用以引发 HTTP 错误，也可以直接调用。
        #:
        #: .. versionadded:: 2.2
        #:     从 ``flask.abort`` 移动，它调用此对象。
        self.aborter = self.make_aborter()

        self.json: JSONProvider = self.json_provider_class(self)
        """提供对 JSON 方法的访问。当应用程序上下文处于活动状态时， ``flask.json`` 中的
        函数将调用此提供程序上的方法。用于处理 JSON 请求和响应。

        :attr:`json_provider_class` 的实例。可以通过更改子类上的该属性或随后分配给该属性
        来进行自定义。

        默认值 :class:`~flask.json.provider.DefaultJSONProvider` 使用 Python 的内置
        :mod:`json` 库。不同的提供者可以使用不同的 JSON 库。

        .. versionadded:: 2.2
        """

        #: 当 :meth:`.url_for` 引发 :exc:`~werkzeug.routing.BuildError` 时，
        #: :meth:`handle_url_build_error` 调用的函数列表。每个函数都通过 ``error`` 、
        #: ``endpoint`` 和 ``values`` 调用。如果函数返回 ``None`` 或引发 ``BuildError``
        #: ，则会跳过该函数。否则，其返回值由 ``url_for`` 返回。
        #:
        #: .. versionadded:: 0.9
        self.url_build_error_handlers: list[
            t.Callable[[Exception, str, dict[str, t.Any]], str]
        ] = []

        #: 应用程序上下文被销毁时调用的函数列表。  由于如果请求结束，应用程序上下文也会
        #: 被拆除，因此这是存储与数据库断开连接的代码的地方。
        #:
        #: .. versionadded:: 0.9
        self.teardown_appcontext_funcs: list[ft.TeardownCallable] = []

        #: 创建 shell 上下文时应运行的 shell 上下文处理器函数的列表。
        #:
        #: .. versionadded:: 0.11
        self.shell_context_processors: list[ft.ShellContextProcessorCallable] = []

        #: 将注册的蓝图名称映射到蓝图对象。该字典保留蓝图注册的顺序。蓝图可以注册多次，
        #: 该字典不跟踪它们附加的频率。
        #:
        #: .. versionadded:: 0.7
        self.blueprints: dict[str, Blueprint] = {}

        #: 扩展可以存储应用程序特定状态的地方。  例如，这是扩展可以存储数据库引擎和类似
        #: 内容的地方。
        #:
        #: 字典键必须与扩展模块的名称匹配。例如，模块 `flask_foo` 中的 "Flask-Foo" 扩展
        #: 应使用 ``'foo'`` 作为键。
        #:
        #: .. versionadded:: 0.7
        self.extensions: dict[str, t.Any] = {}

        #: 此实例的 :class:`~werkzeug.routing.Map` 。  您可以在创建类之后但在连接任何路
        #: 由之前使用它来更改路由转换器。  示例::
        #:
        #:    from werkzeug.routing import BaseConverter
        #:
        #:    class ListConverter(BaseConverter):
        #:        def to_python(self, value):
        #:            return value.split(',')
        #:        def to_url(self, values):
        #:            return ','.join(super(ListConverter, self).to_url(value)
        #:                            for value in values)
        #:
        #:    app = Flask(__name__)
        #:    app.url_map.converters['list'] = ListConverter
        self.url_map = self.url_map_class(host_matching=host_matching)

        self.subdomain_matching = subdomain_matching

        # 在内部跟踪应用程序是否已处理至少一个请求。
        self._got_first_request = False

    def _check_setup_finished(self, f_name: str) -> None:
        if self._got_first_request:
            raise AssertionError(
                f"The setup method '{f_name}' can no longer be called"
                " on the application. It has already handled its first"
                " request, any changes will not be applied"
                " consistently.\n"
                "Make sure all imports, decorators, functions, etc."
                " needed to set up the application are done before"
                " running it."
            )

    @cached_property
    def name(self) -> str:
        """应用程序的名称。  这通常是导入名称，不同之处在于，如果导入名称是 main，则从运
        行文件中猜测。  当 Flask 需要应用程序的名称时，此名称用作显示名称。  可以设置和
        覆盖它来更改值。

        .. versionadded:: 0.8
        """
        if self.import_name == "__main__":
            fn: str | None = getattr(sys.modules["__main__"], "__file__", None)
            if fn is None:
                return "__main__"
            return os.path.splitext(os.path.basename(fn))[0]
        return self.import_name

    @cached_property
    def logger(self) -> logging.Logger:
        """应用程序的标准 Python :class:`~logging.Logger` ，与 :attr:`name` 同名。

        在调试模式下，记录器的 :attr:`~logging.Logger.level` 将设置为
        :data:`~logging.DEBUG` 。

        如果没有配置处理程序，则会添加默认处理程序。有关详细信息，请参阅 :doc:`/logging`
        。

        .. versionchanged:: 1.1.0
            记录器采用与 :attr:`name` 相同的名称，而不是硬编码 ``"flask.app"`` 。

        .. versionchanged:: 1.0.0
            行为被简化。记录器始终命名为 ``"flask.app"`` 。该级别仅在配置期间设置，不会
            每次都检查 ``app.debug`` 。仅使用一种格式，不使用不同的格式，具体取决于
            ``app.debug`` 。不会删除任何处理程序，并且仅在尚未配置任何处理程序时才会添加
            处理程序。

        .. versionadded:: 0.3
        """
        return create_logger(self)

    @cached_property
    def jinja_env(self) -> Environment:
        """用于加载模板的 Jinja 环境。

        第一次访问此属性时会创建环境。此后更改 :attr:`jinja_options` 将不起作用。
        """
        return self.create_jinja_environment()

    def create_jinja_environment(self) -> Environment:
        raise NotImplementedError()

    def make_config(self, instance_relative: bool = False) -> Config:
        """用于通过 Flask 构造函数创建配置属性。 `instance_relative` 参数从 Flask （名为
        `instance_relative_config` ）的构造函数传入，并指示配置是否应相对于实例路径或应
        用程序的根路径。

        .. versionadded:: 0.8
        """
        root_path = self.root_path
        if instance_relative:
            root_path = self.instance_path
        defaults = dict(self.default_config)
        defaults["DEBUG"] = get_debug_flag()
        return self.config_class(root_path, defaults)

    def make_aborter(self) -> Aborter:
        """创建要分配给 :attr:`aborter` 的对象。该对象由 :func:`flask.abort` 调用以引发
        HTTP 错误，也可以直接调用。

        默认情况下，这会创建 :attr:`aborter_class` 的实例，默认为
        :class:`werkzeug.exceptions.Aborter` 。

        .. versionadded:: 2.2
        """
        return self.aborter_class()

    def auto_find_instance_path(self) -> str:
        """如果未将实例路径提供给应用程序类的构造函数，则尝试查找实例路径。  它基本上会
        计算主文件或包旁边名为 ``instance`` 的文件夹的路径。

        .. versionadded:: 0.8
        """
        prefix, package_path = find_package(self.import_name)
        if prefix is None:
            return os.path.join(package_path, "instance")
        return os.path.join(prefix, "var", f"{self.name}-instance")

    def create_global_jinja_loader(self) -> DispatchingJinjaLoader:
        """为 Jinja 环境创建加载程序。  可用于仅覆盖加载程序并保持其余部分不变。  不鼓励
        重写此函数。  相反，我们应该重写 :meth:`jinja_loader` 函数。

        全局加载器在应用程序的加载器和各个蓝图之间进行调度。

        .. versionadded:: 0.7
        """
        return DispatchingJinjaLoader(self)

    def select_jinja_autoescape(self, filename: str | None) -> bool:
        """如果给定模板名称的自动转义应处于活动状态，则返回 ``True`` 。如果未给出模板名
        称，则返回 `True` 。

        .. versionchanged:: 3.2
            使用不区分大小写的比较而不是仅使用小写字母。

        .. versionchanged:: 2.2
            现在默认为 ``.svg`` 文件启用自动转义。

        .. versionadded:: 0.5
        """
        if filename is None:
            return True
        return filename.lower().endswith((".html", ".htm", ".xml", ".xhtml", ".svg"))

    @property
    def debug(self) -> bool:
        """是否启用调试模式。当使用 ``flask run`` 启动开发服务器时，将显示一个交互式调试
        器以查找未处理的异常，并且当代码更改时将重新加载服务器。这映射到 :data:`DEBUG`
        配置键。如果设置较晚，它可能不会按预期运行。

        **在生产中部署时不要启用调试模式。**

        默认值： ``False``
        """
        return self.config["DEBUG"]  # type: ignore[no-any-return]

    @debug.setter
    def debug(self, value: bool) -> None:
        self.config["DEBUG"] = value

        if self.config["TEMPLATES_AUTO_RELOAD"] is None:
            self.jinja_env.auto_reload = value

    @setupmethod
    def register_blueprint(self, blueprint: Blueprint, **options: t.Any) -> None:
        """在应用程序上注册 :class:`~flask.Blueprint` 。传递给此方法的关键字参数将覆盖蓝
        图上设置的默认值。

        将蓝图记录到应用程序的 :attr:`blueprints` 中后，调用蓝图的
        :meth:`~flask.Blueprint.register` 方法。

        :param blueprint: 要注册的蓝图。
        :param url_prefix: Blueprint 路由将以此为前缀。
        :param subdomain: Blueprint 路由将在此子域上匹配。
        :param url_defaults: Blueprint 路由将使用这些默认值作为视图参数。
        :param options: 其他关键字参数传递给
            :class:`~flask.blueprints.BlueprintSetupState` 。可以在
            :meth:`~flask.Blueprint.record` 回调中访问它们。

        .. versionchanged:: 2.0.1
            ``name`` 选项可用于更改注册蓝图的（预点）名称。这允许使用 ``url_for`` 的唯一
            名称多次注册相同的蓝图。

        .. versionadded:: 0.7
        """
        blueprint.register(self, options)

    def iter_blueprints(self) -> t.ValuesView[Blueprint]:
        """按注册顺序迭代所有蓝图。

        .. versionadded:: 0.11
        """
        return self.blueprints.values()

    @setupmethod
    def add_url_rule(
        self,
        rule: str,
        endpoint: str | None = None,
        view_func: ft.RouteCallable | None = None,
        provide_automatic_options: bool | None = None,
        **options: t.Any,
    ) -> None:
        if endpoint is None:
            endpoint = _endpoint_from_view_func(view_func)  # type: ignore
        options["endpoint"] = endpoint
        methods = options.pop("methods", None)

        # 如果没有给出方法并且 view_func 对象知道它的方法，我们可以使用它。  如果两者都
        # 不存在，我们将使用仅 ``GET`` 的元组作为默认值。
        if methods is None:
            methods = getattr(view_func, "methods", None) or ("GET",)
        if isinstance(methods, str):
            raise TypeError(
                "Allowed methods must be a list of strings, for"
                ' example: @app.route(..., methods=["POST"])'
            )
        methods = {item.upper() for item in methods}

        # 应始终添加的方法
        required_methods: set[str] = set(getattr(view_func, "required_methods", ()))

        if provide_automatic_options is None:
            provide_automatic_options = getattr(
                view_func, "provide_automatic_options", None
            )

            if provide_automatic_options is None:
                provide_automatic_options = (
                    "OPTIONS" not in methods
                    and self.config["PROVIDE_AUTOMATIC_OPTIONS"]
                )

        if provide_automatic_options:
            required_methods.add("OPTIONS")

        # 现在添加所需的方法。
        methods |= required_methods

        rule_obj = self.url_rule_class(rule, methods=methods, **options)
        rule_obj.provide_automatic_options = provide_automatic_options  # type: ignore[attr-defined]

        self.url_map.add(rule_obj)
        if view_func is not None:
            old_func = self.view_functions.get(endpoint)
            if old_func is not None and old_func != view_func:
                raise AssertionError(
                    "View function mapping is overwriting an existing"
                    f" endpoint function: {endpoint}"
                )
            self.view_functions[endpoint] = view_func

    @t.overload
    def template_filter(self, name: T_template_filter) -> T_template_filter: ...
    @t.overload
    def template_filter(
        self, name: str | None = None
    ) -> t.Callable[[T_template_filter], T_template_filter]: ...
    @setupmethod
    def template_filter(
        self, name: T_template_filter | str | None = None
    ) -> T_template_filter | t.Callable[[T_template_filter], T_template_filter]:
        """装饰一个函数以将其注册为自定义 Jinja 过滤器。该名称是可选的。装饰器可以在没有
        括号的情况下使用。

        .. code-block:: python

            @app.template_filter("reverse")
            def reverse_filter(s):
                return reversed(s)

        :meth:`add_template_filter` 方法可用于稍后注册函数而不是装饰。

        :param name: 将过滤器注册为的名称。如果未给出，则使用函数的名称。
        """
        if callable(name):
            self.add_template_filter(name)
            return name

        def decorator(f: T_template_filter) -> T_template_filter:
            self.add_template_filter(f, name=name)
            return f

        return decorator

    @setupmethod
    def add_template_filter(
        self, f: ft.TemplateFilterCallable, name: str | None = None
    ) -> None:
        """注册一个函数以用作自定义 Jinja 过滤器。

        :meth:`template_filter` 装饰器可用于通过装饰来注册函数。

        :param f: 注册的函数。
        :param name: 将过滤器注册为的名称。如果未给出，则使用函数的名称。
        """
        self.jinja_env.filters[name or f.__name__] = f

    @t.overload
    def template_test(self, name: T_template_test) -> T_template_test: ...
    @t.overload
    def template_test(
        self, name: str | None = None
    ) -> t.Callable[[T_template_test], T_template_test]: ...
    @setupmethod
    def template_test(
        self, name: T_template_test | str | None = None
    ) -> T_template_test | t.Callable[[T_template_test], T_template_test]:
        """装饰一个函数以将其注册为自定义 Jinja 测试。该名称是可选的。装饰器可以在没有括
        号的情况下使用。

        .. code-block:: python

            @app.template_test("prime")
            def is_prime_test(n):
                if n == 2:
                    return True
                for i in range(2, int(math.ceil(math.sqrt(n))) + 1):
                    if n % i == 0:
                        return False
              return True

        :meth:`add_template_test` 方法可用于稍后注册函数而不是装饰。

        :param name: 将过滤器注册为的名称。如果未给出，则使用函数的名称。

        .. versionadded:: 0.10
        """
        if callable(name):
            self.add_template_test(name)
            return name

        def decorator(f: T_template_test) -> T_template_test:
            self.add_template_test(f, name=name)
            return f

        return decorator

    @setupmethod
    def add_template_test(
        self, f: ft.TemplateTestCallable, name: str | None = None
    ) -> None:
        """注册一个函数以用作自定义 Jinja 测试。

        :meth:`template_test` 装饰器可用于通过装饰来注册函数。

        :param f: 注册的函数。
        :param name: 注册测试的名称。如果未给出，则使用函数的名称。

        .. versionadded:: 0.10
        """
        self.jinja_env.tests[name or f.__name__] = f

    @t.overload
    def template_global(self, name: T_template_global) -> T_template_global: ...
    @t.overload
    def template_global(
        self, name: str | None = None
    ) -> t.Callable[[T_template_global], T_template_global]: ...
    @setupmethod
    def template_global(
        self, name: T_template_global | str | None = None
    ) -> T_template_global | t.Callable[[T_template_global], T_template_global]:
        """装饰一个函数以将其注册为自定义 Jinja 全局函数。该名称是可选的。装饰器可以在没
        有括号的情况下使用。

        .. code-block:: python

            @app.template_global
            def double(n):
                return 2 * n

        :meth:`add_template_global` 方法可用于稍后注册函数而不是装饰。

        :param name: 将全局注册为的名称。如果未给出，则使用函数的名称。

        .. versionadded:: 0.10
        """
        if callable(name):
            self.add_template_global(name)
            return name

        def decorator(f: T_template_global) -> T_template_global:
            self.add_template_global(f, name=name)
            return f

        return decorator

    @setupmethod
    def add_template_global(
        self, f: ft.TemplateGlobalCallable, name: str | None = None
    ) -> None:
        """注册一个函数以用作自定义 Jinja 全局函数。

        :meth:`template_global` 装饰器可用于通过装饰来注册函数。

        :param f: 注册的函数。
        :param name: 将全局注册为的名称。如果未给出，则使用函数的名称。

        .. versionadded:: 0.10
        """
        self.jinja_env.globals[name or f.__name__] = f

    @setupmethod
    def teardown_appcontext(self, f: T_teardown) -> T_teardown:
        """注册一个在弹出应用程序上下文时调用的函数。上下文在请求、CLI 命令或手动
        ``with`` 块结束时弹出。

        .. code-block:: python

            with app.app_context():
                ...

        当 ``with`` 块退出（或调用 ``ctx.pop()`` ）时，将在应用程序上下文变为非活动状态
        之前调用清理函数。

        当由于未处理的异常而调用清理函数时，它将传递一个错误对象。如果注册了
        :meth:`errorhandler` ，它将处理异常，而清理将不会收到它。

        清理函数必须避免引发异常。如果它们执行可能失败的代码，则必须用 ``try`` /
        ``except`` 块包围该代码并记录所有错误。

        清理函数的返回值将被忽略。

        .. versionadded:: 0.9
        """
        self.teardown_appcontext_funcs.append(f)
        return f

    @setupmethod
    def shell_context_processor(
        self, f: T_shell_context_processor
    ) -> T_shell_context_processor:
        """注册 shell 上下文处理器函数。

        .. versionadded:: 0.11
        """
        self.shell_context_processors.append(f)
        return f

    def _find_error_handler(
        self, e: Exception, blueprints: list[str]
    ) -> ft.ErrorHandlerCallable | None:
        """按以下顺序返回异常的已注册错误处理程序：特定代码的蓝图处理程序、特定代码的应
        用程序处理程序、异常类的蓝图处理程序、异常类的应用程序处理程序，如果未找到合适的
        处理程序，则返回 ``None`` 。
        """
        exc_class, code = self._get_exc_class_and_code(type(e))
        names = (*blueprints, None)

        for c in (code, None) if code is not None else (None,):
            for name in names:
                handler_map = self.error_handler_spec[name][c]

                if not handler_map:
                    continue

                for cls in exc_class.__mro__:
                    handler = handler_map.get(cls)

                    if handler is not None:
                        return handler
        return None

    def trap_http_exception(self, e: Exception) -> bool:
        """检查是否应捕获 HTTP 异常。  默认情况下，如果 ``TRAP_BAD_REQUEST_ERRORS`` 设置
        为 ``True`` ，则除错误请求密钥错误外，所有异常都会返回 ``False`` 。  如果
        ``TRAP_HTTP_EXCEPTIONS`` 设置为 ``True`` ，它还会返回 ``True`` 。

        这是针对视图函数引发的所有 HTTP 异常调用的。如果它为任何异常返回 ``True`` ，则不
        会调用该异常的错误处理程序，并且它在回溯中显示为常规异常。  这对于调试隐式引发的
        HTTP 异常很有帮助。

        .. versionchanged:: 1.0
            默认情况下，在调试模式下不会捕获不良请求错误。

        .. versionadded:: 0.8
        """
        if self.config["TRAP_HTTP_EXCEPTIONS"]:
            return True

        trap_bad_request = self.config["TRAP_BAD_REQUEST_ERRORS"]

        # 如果未设置，则在调试模式下捕获关键错误
        if (
            trap_bad_request is None
            and self.debug
            and isinstance(e, BadRequestKeyError)
        ):
            return True

        if trap_bad_request:
            return isinstance(e, BadRequest)

        return False

    should_ignore_error: None = None
    """如果此方法返回 ``True`` ，则错误将不会传递给清理处理程序，并且不会保留上下文以进
    行调试。

    .. deprecated:: 3.2
        根据需要在清理处理程序中处理错误。

    .. versionadded:: 0.10
    """

    def redirect(self, location: str, code: int = 303) -> BaseResponse:
        """创建重定向响应对象。

        这是由 :func:`flask.redirect` 调用的，也可以直接调用。

        :param location: 要重定向到的 URL。
        :param code: 重定向的状态代码。

        .. versionchanged:: 3.2
            ``code`` 默认为 ``303`` 而不是 ``302`` 。

        .. versionadded:: 2.2
            从 ``flask.redirect`` 移出，它调用此方法。
        """
        return _wz_redirect(
            location,
            code=code,
            Response=self.response_class,  # type: ignore[arg-type]
        )

    def inject_url_defaults(self, endpoint: str, values: dict[str, t.Any]) -> None:
        """将给定端点的 URL 默认值直接注入到传递的值字典中。  这是内部使用的，并在 URL
        构建中自动调用。

        .. versionadded:: 0.7
        """
        names: t.Iterable[str | None] = (None,)

        # url_for 可以在请求上下文之外调用，解析传递的端点而不是使用 request.blueprints
        # 。
        if "." in endpoint:
            names = chain(
                names, reversed(_split_blueprint_path(endpoint.rpartition(".")[0]))
            )

        for name in names:
            if name in self.url_default_functions:
                for func in self.url_default_functions[name]:
                    func(endpoint, values)

    def handle_url_build_error(
        self, error: BuildError, endpoint: str, values: dict[str, t.Any]
    ) -> str:
        """如果引发了 :exc:`~werkzeug.routing.BuildError` ，则由 :meth:`.url_for` 调用。
        如果返回一个值，它将由 ``url_for`` 返回，否则将重新引发错误。

        :attr:`url_build_error_handlers` 中的每个函数都通过 ``error`` 、 ``endpoint`` 和
        ``values`` 调用。如果函数返回 ``None`` 或引发 ``BuildError`` ，则会跳过该函数。
        否则，其返回值由 ``url_for`` 返回。

        :param error: 正在处理活动的 ``BuildError`` 。
        :param endpoint: 正在构建端点。
        :param values: 传递给 ``url_for`` 的关键字参数。
        """
        for handler in self.url_build_error_handlers:
            try:
                rv = handler(error, endpoint, values)
            except BuildError as e:
                # 使错误在除块之外可用
                error = e
            else:
                if rv is not None:
                    return rv

        # 如果使用活动异常调用，则重新引发，否则引发传入的异常。
        if error is sys.exc_info()[1]:
            raise

        raise error
