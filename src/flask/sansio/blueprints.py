from __future__ import annotations

import os
import typing as t
from collections import defaultdict
from functools import update_wrapper

from .. import typing as ft
from .scaffold import _endpoint_from_view_func
from .scaffold import _sentinel
from .scaffold import Scaffold
from .scaffold import setupmethod

if t.TYPE_CHECKING:  # pragma: no cover
    from .app import App

DeferredSetupFunction = t.Callable[["BlueprintSetupState"], None]
T_after_request = t.TypeVar("T_after_request", bound=ft.AfterRequestCallable[t.Any])
T_before_request = t.TypeVar("T_before_request", bound=ft.BeforeRequestCallable)
T_error_handler = t.TypeVar("T_error_handler", bound=ft.ErrorHandlerCallable)
T_teardown = t.TypeVar("T_teardown", bound=ft.TeardownCallable)
T_template_context_processor = t.TypeVar(
    "T_template_context_processor", bound=ft.TemplateContextProcessorCallable
)
T_template_filter = t.TypeVar("T_template_filter", bound=ft.TemplateFilterCallable)
T_template_global = t.TypeVar("T_template_global", bound=ft.TemplateGlobalCallable)
T_template_test = t.TypeVar("T_template_test", bound=ft.TemplateTestCallable)
T_url_defaults = t.TypeVar("T_url_defaults", bound=ft.URLDefaultCallable)
T_url_value_preprocessor = t.TypeVar(
    "T_url_value_preprocessor", bound=ft.URLValuePreprocessorCallable
)


class BlueprintSetupState:
    """用于向应用程序注册蓝图的临时持有者对象。  此类的实例由
    :meth:`~flask.Blueprint.make_setup_state` 方法创建，然后传递给所有注册回调函数。
    """

    def __init__(
        self,
        blueprint: Blueprint,
        app: App,
        options: t.Any,
        first_registration: bool,
    ) -> None:
        #: 对当前应用程序的引用
        self.app = app

        #: 对创建此设置状态的蓝图的引用。
        self.blueprint = blueprint

        #: 包含传递给 :meth:`~flask.Flask.register_blueprint` 方法的所有选项的字典。
        self.options = options

        #: 由于蓝图可以在应用程序中多次注册，并且并非所有内容都希望在其上多次注册，因此
        #: 此属性可用于确定蓝图过去是否已注册。
        self.first_registration = first_registration

        subdomain = self.options.get("subdomain")
        if subdomain is None:
            subdomain = self.blueprint.subdomain

        #: 蓝图应激活的子域，否则为 ``None`` 。
        self.subdomain = subdomain

        url_prefix = self.options.get("url_prefix")
        if url_prefix is None:
            url_prefix = self.blueprint.url_prefix
        #: 应用于蓝图上定义的所有 URL 的前缀。
        self.url_prefix = url_prefix

        self.name = self.options.get("name", blueprint.name)
        self.name_prefix = self.options.get("name_prefix", "")

        #: 具有 URL 默认值的字典将添加到使用蓝图定义的每个 URL 中。
        self.url_defaults = dict(self.blueprint.url_values_defaults)
        self.url_defaults.update(self.options.get("url_defaults", ()))

    def add_url_rule(
        self,
        rule: str,
        endpoint: str | None = None,
        view_func: ft.RouteCallable | None = None,
        **options: t.Any,
    ) -> None:
        """用于向应用程序注册规则（以及可选的视图函数）的辅助方法。  端点自动以蓝图名称
        作为前缀。
        """
        if self.url_prefix is not None:
            if rule:
                rule = "/".join((self.url_prefix.rstrip("/"), rule.lstrip("/")))
            else:
                rule = self.url_prefix
        options.setdefault("subdomain", self.subdomain)
        if endpoint is None:
            endpoint = _endpoint_from_view_func(view_func)  # type: ignore
        defaults = self.url_defaults
        if "defaults" in options:
            defaults = dict(defaults, **options.pop("defaults"))

        self.app.add_url_rule(
            rule,
            f"{self.name_prefix}.{self.name}.{endpoint}".lstrip("."),
            view_func,
            defaults=defaults,
            **options,
        )


class Blueprint(Scaffold):
    """代表一个蓝图、一系列路由和其他与应用程序相关的功能，可以稍后在实际应用程序上注册
    。

    蓝图是一个允许定义应用程序功能而无需提前提供应用程序对象的对象。它使用与
    :class:`~flask.Flask` 相同的装饰器，但通过记录它们以供以后注册来推迟对应用程序的需求
    。

    使用蓝图装饰函数会创建一个延迟函数，当蓝图在应用程序上注册时，会使用
    :class:`~flask.blueprints.BlueprintSetupState` 调用该函数。

    有关详细信息，请参阅 :doc:`/blueprints` 。

    :param name: 蓝图的名称。将添加到每个端点名称前面。
    :param import_name: 蓝图包的名称，通常为 ``__name__`` 。这有助于找到蓝图的
        ``root_path`` 。
    :param static_folder: 包含静态文件的文件夹，应由蓝图的静态路由提供服务。该路径相对于
        蓝图的根路径。默认情况下禁用 Blueprint 静态文件。
    :param static_url_path: 提供静态文件的 URL。默认为 ``static_folder`` 。如果蓝图没有
        ``url_prefix`` ，则应用程序的静态路由将优先，并且蓝图的静态文件将不可访问。
    :param template_folder: 包含模板的文件夹，应将其添加到应用程序的模板搜索路径中。该路
        径相对于蓝图的根路径。默认情况下禁用 Blueprint 模板。 Blueprint 模板的优先级低于
        应用程序模板文件夹中的模板。
    :param url_prefix: 附加到所有蓝图 URL 的路径，使它们与应用程序的其余路由不同。
    :param subdomain: 蓝图路由默认匹配的子域。
    :param url_defaults: 蓝图路由默认接收的默认值的字典。
    :param root_path: 默认情况下，蓝图将根据 ``import_name`` 自动设置此项。在某些情况下
        ，此自动检测可能会失败，因此可以手动指定路径。

    .. versionchanged:: 1.1.0
        蓝图有一个 ``cli`` 组来注册嵌套的 CLI 命令。 ``cli_group`` 参数控制 ``flask`` 命
        令下的组的名称。

    .. versionadded:: 0.7
    """

    _got_registered_once = False

    def __init__(
        self,
        name: str,
        import_name: str,
        static_folder: str | os.PathLike[str] | None = None,
        static_url_path: str | None = None,
        template_folder: str | os.PathLike[str] | None = None,
        url_prefix: str | None = None,
        subdomain: str | None = None,
        url_defaults: dict[str, t.Any] | None = None,
        root_path: str | None = None,
        cli_group: str | None = _sentinel,  # type: ignore[assignment]
    ):
        super().__init__(
            import_name=import_name,
            static_folder=static_folder,
            static_url_path=static_url_path,
            template_folder=template_folder,
            root_path=root_path,
        )

        if not name:
            raise ValueError("'name' may not be empty.")

        if "." in name:
            raise ValueError("'name' may not contain a dot '.' character.")

        self.name = name
        self.url_prefix = url_prefix
        self.subdomain = subdomain
        self.deferred_functions: list[DeferredSetupFunction] = []

        if url_defaults is None:
            url_defaults = {}

        self.url_values_defaults = url_defaults
        self.cli_group = cli_group
        self._blueprints: list[tuple[Blueprint, dict[str, t.Any]]] = []

    def _check_setup_finished(self, f_name: str) -> None:
        if self._got_registered_once:
            raise AssertionError(
                f"The setup method '{f_name}' can no longer be called on the blueprint"
                f" '{self.name}'. It has already been registered at least once, any"
                " changes will not be applied consistently.\n"
                "Make sure all imports, decorators, functions, etc. needed to set up"
                " the blueprint are done before registering it."
            )

    @setupmethod
    def record(self, func: DeferredSetupFunction) -> None:
        """注册在应用程序上注册蓝图时调用的函数。  使用 :meth:`make_setup_state` 方法返
        回的状态作为参数来调用此函数。
        """
        self.deferred_functions.append(func)

    @setupmethod
    def record_once(self, func: DeferredSetupFunction) -> None:
        """与 :meth:`record` 类似，但将函数包装在另一个函数中，以确保该函数仅被调用一次
        。  如果蓝图在应用程序上第二次注册，则不会调用传递的函数。
        """

        def wrapper(state: BlueprintSetupState) -> None:
            if state.first_registration:
                func(state)

        self.record(update_wrapper(wrapper, func))

    def make_setup_state(
        self, app: App, options: dict[str, t.Any], first_registration: bool = False
    ) -> BlueprintSetupState:
        """创建 :meth:`~flask.blueprints.BlueprintSetupState` 对象的实例，稍后将其传递给
        注册回调函数。子类可以重写它以返回设置状态的子类。
        """
        return BlueprintSetupState(self, app, options, first_registration)

    @setupmethod
    def register_blueprint(self, blueprint: Blueprint, **options: t.Any) -> None:
        """在此蓝图上注册一个 :class:`~flask.Blueprint` 。传递给此方法的关键字参数将覆盖
        蓝图上设置的默认值。

        .. versionchanged:: 2.0.1
            ``name`` 选项可用于更改注册蓝图的（预点）名称。这允许使用 ``url_for`` 的唯一
            名称多次注册相同的蓝图。

        .. versionadded:: 2.0
        """
        if blueprint is self:
            raise ValueError("Cannot register a blueprint on itself")
        self._blueprints.append((blueprint, options))

    def register(self, app: App, options: dict[str, t.Any]) -> None:
        """由 :meth:`Flask.register_blueprint` 调用，以向应用程序注册在蓝图上注册的所有
        视图和回调。创建一个 :class:`.BlueprintSetupState` 并用它调用每个 :meth:`record`
        回调。

        :param app: 该蓝图正在注册的应用程序。
        :param options: 从 :meth:`~Flask.register_blueprint` 转发的关键字参数。

        .. versionchanged:: 2.3
            嵌套蓝图现在可以正确应用子域。

        .. versionchanged:: 2.1
            多次使用相同的名称注册相同的蓝图是错误的。

        .. versionchanged:: 2.0.1
            嵌套蓝图用其点名称注册。这允许具有相同名称的不同蓝图嵌套在不同位置。

        .. versionchanged:: 2.0.1
            ``name`` 选项可用于更改注册蓝图的（预点）名称。这允许使用 ``url_for`` 的唯一
            名称多次注册相同的蓝图。
        """
        name_prefix = options.get("name_prefix", "")
        self_name = options.get("name", self.name)
        name = f"{name_prefix}.{self_name}".lstrip(".")

        if name in app.blueprints:
            bp_desc = "this" if app.blueprints[name] is self else "a different"
            existing_at = f" '{name}'" if self_name != name else ""

            raise ValueError(
                f"The name '{self_name}' is already registered for"
                f" {bp_desc} blueprint{existing_at}. Use 'name=' to"
                f" provide a unique name."
            )

        first_bp_registration = not any(bp is self for bp in app.blueprints.values())
        first_name_registration = name not in app.blueprints

        app.blueprints[name] = self
        self._got_registered_once = True
        state = self.make_setup_state(app, options, first_bp_registration)

        if self.has_static_folder:
            state.add_url_rule(
                f"{self.static_url_path}/<path:filename>",
                view_func=self.send_static_file,  # type: ignore[attr-defined]
                endpoint="static",
            )

        # 将蓝图数据合并到父级中。
        if first_bp_registration or first_name_registration:
            self._merge_blueprint_funcs(app, name)

        for deferred in self.deferred_functions:
            deferred(state)

        cli_resolved_group = options.get("cli_group", self.cli_group)

        if self.cli.commands:
            if cli_resolved_group is None:
                app.cli.commands.update(self.cli.commands)
            elif cli_resolved_group is _sentinel:
                self.cli.name = name
                app.cli.add_command(self.cli)
            else:
                self.cli.name = cli_resolved_group
                app.cli.add_command(self.cli)

        for blueprint, bp_options in self._blueprints:
            bp_options = bp_options.copy()
            bp_url_prefix = bp_options.get("url_prefix")
            bp_subdomain = bp_options.get("subdomain")

            if bp_subdomain is None:
                bp_subdomain = blueprint.subdomain

            if state.subdomain is not None and bp_subdomain is not None:
                bp_options["subdomain"] = bp_subdomain + "." + state.subdomain
            elif bp_subdomain is not None:
                bp_options["subdomain"] = bp_subdomain
            elif state.subdomain is not None:
                bp_options["subdomain"] = state.subdomain

            if bp_url_prefix is None:
                bp_url_prefix = blueprint.url_prefix

            if state.url_prefix is not None and bp_url_prefix is not None:
                bp_options["url_prefix"] = (
                    state.url_prefix.rstrip("/") + "/" + bp_url_prefix.lstrip("/")
                )
            elif bp_url_prefix is not None:
                bp_options["url_prefix"] = bp_url_prefix
            elif state.url_prefix is not None:
                bp_options["url_prefix"] = state.url_prefix

            bp_options["name_prefix"] = name
            blueprint.register(app, bp_options)

    def _merge_blueprint_funcs(self, app: App, name: str) -> None:
        def extend(
            bp_dict: dict[ft.AppOrBlueprintKey, list[t.Any]],
            parent_dict: dict[ft.AppOrBlueprintKey, list[t.Any]],
        ) -> None:
            for key, values in bp_dict.items():
                key = name if key is None else f"{name}.{key}"
                parent_dict[key].extend(values)

        for key, value in self.error_handler_spec.items():
            key = name if key is None else f"{name}.{key}"
            value = defaultdict(
                dict,
                {
                    code: {exc_class: func for exc_class, func in code_values.items()}
                    for code, code_values in value.items()
                },
            )
            app.error_handler_spec[key] = value

        for endpoint, func in self.view_functions.items():
            app.view_functions[endpoint] = func

        extend(self.before_request_funcs, app.before_request_funcs)
        extend(self.after_request_funcs, app.after_request_funcs)
        extend(
            self.teardown_request_funcs,
            app.teardown_request_funcs,
        )
        extend(self.url_default_functions, app.url_default_functions)
        extend(self.url_value_preprocessors, app.url_value_preprocessors)
        extend(self.template_context_processors, app.template_context_processors)

    @setupmethod
    def add_url_rule(
        self,
        rule: str,
        endpoint: str | None = None,
        view_func: ft.RouteCallable | None = None,
        provide_automatic_options: bool | None = None,
        **options: t.Any,
    ) -> None:
        """使用蓝图注册 URL 规则。有关完整文档，请参阅 :meth:`.Flask.add_url_rule` 。

        URL 规则以蓝图的 URL 前缀为前缀。与 :func:`url_for` 一起使用的端点名称以蓝图名称
        为前缀。
        """
        if endpoint and "." in endpoint:
            raise ValueError("'endpoint' may not contain a dot '.' character.")

        if view_func and hasattr(view_func, "__name__") and "." in view_func.__name__:
            raise ValueError("'view_func' name may not contain a dot '.' character.")

        self.record(
            lambda s: s.add_url_rule(
                rule,
                endpoint,
                view_func,
                provide_automatic_options=provide_automatic_options,
                **options,
            )
        )

    @t.overload
    def app_template_filter(self, name: T_template_filter) -> T_template_filter: ...
    @t.overload
    def app_template_filter(
        self, name: str | None = None
    ) -> t.Callable[[T_template_filter], T_template_filter]: ...
    @setupmethod
    def app_template_filter(
        self, name: T_template_filter | str | None = None
    ) -> T_template_filter | t.Callable[[T_template_filter], T_template_filter]:
        """装饰一个函数以将其注册为自定义 Jinja 过滤器。该名称是可选的。装饰器可以在没有
        括号的情况下使用。

        :meth:`add_app_template_filter` 方法可用于稍后注册函数而不是装饰。

        该过滤器适用于所有模板，而不仅仅是此蓝图下的模板。相当于
        :meth:`.Flask.template_filter` 。

        :param name: 将过滤器注册为的名称。如果未给出，则使用函数的名称。
        """
        if callable(name):
            self.add_app_template_filter(name)
            return name

        def decorator(f: T_template_filter) -> T_template_filter:
            self.add_app_template_filter(f, name=name)
            return f

        return decorator

    @setupmethod
    def add_app_template_filter(
        self, f: ft.TemplateFilterCallable, name: str | None = None
    ) -> None:
        """注册一个函数以用作自定义 Jinja 过滤器。

        :meth:`app_template_filter` 装饰器可用于通过装饰来注册函数。

        该过滤器适用于所有模板，而不仅仅是此蓝图下的模板。相当于
        :meth:`.Flask.add_template_filter` 。

        :param f: 注册的函数。
        :param name: 将过滤器注册为的名称。如果未给出，则使用函数的名称。
        """

        def register_template_filter(state: BlueprintSetupState) -> None:
            state.app.add_template_filter(f, name=name)

        self.record_once(register_template_filter)

    @t.overload
    def app_template_test(self, name: T_template_test) -> T_template_test: ...
    @t.overload
    def app_template_test(
        self, name: str | None = None
    ) -> t.Callable[[T_template_test], T_template_test]: ...
    @setupmethod
    def app_template_test(
        self, name: T_template_test | str | None = None
    ) -> T_template_test | t.Callable[[T_template_test], T_template_test]:
        """装饰一个函数以将其注册为自定义 Jinja 测试。该名称是可选的。装饰器可以在没有括
        号的情况下使用。

        :meth:`add_app_template_test` 方法可用于稍后注册函数而不是装饰。

        该测试适用于所有模板，而不仅仅是此蓝图下的模板。相当于
        :meth:`.Flask.template_test` 。

        :param name: 将过滤器注册为的名称。如果未给出，则使用函数的名称。

        .. versionadded:: 0.10
        """
        if callable(name):
            self.add_app_template_test(name)
            return name

        def decorator(f: T_template_test) -> T_template_test:
            self.add_app_template_test(f, name=name)
            return f

        return decorator

    @setupmethod
    def add_app_template_test(
        self, f: ft.TemplateTestCallable, name: str | None = None
    ) -> None:
        """注册一个函数以用作自定义 Jinja 测试。

        :meth:`app_template_test` 装饰器可用于通过装饰来注册函数。

        该测试适用于所有模板，而不仅仅是此蓝图下的模板。相当于
        :meth:`.Flask.add_template_test` 。

        :param f: 注册的函数。
        :param name: 注册测试的名称。如果未给出，则使用函数的名称。

        .. versionadded:: 0.10
        """

        def register_template_test(state: BlueprintSetupState) -> None:
            state.app.add_template_test(f, name=name)

        self.record_once(register_template_test)

    @t.overload
    def app_template_global(self, name: T_template_global) -> T_template_global: ...
    @t.overload
    def app_template_global(
        self, name: str | None = None
    ) -> t.Callable[[T_template_global], T_template_global]: ...
    @setupmethod
    def app_template_global(
        self, name: T_template_global | str | None = None
    ) -> T_template_global | t.Callable[[T_template_global], T_template_global]:
        """装饰一个函数以将其注册为自定义 Jinja 全局函数。该名称是可选的。装饰器可以在没
        有括号的情况下使用。

        :meth:`add_app_template_global` 方法可用于稍后注册函数而不是装饰。

        全局在所有模板中都可用，而不仅仅是此蓝图下的模板。相当于
        :meth:`.Flask.template_global` 。

        :param name: 将全局注册为的名称。如果未给出，则使用函数的名称。

        .. versionadded:: 0.10
        """
        if callable(name):
            self.add_app_template_global(name)
            return name

        def decorator(f: T_template_global) -> T_template_global:
            self.add_app_template_global(f, name=name)
            return f

        return decorator

    @setupmethod
    def add_app_template_global(
        self, f: ft.TemplateGlobalCallable, name: str | None = None
    ) -> None:
        """注册一个函数以用作自定义 Jinja 全局函数。

        :meth:`app_template_global` 装饰器可用于通过装饰来注册函数。

        全局在所有模板中都可用，而不仅仅是此蓝图下的模板。相当于
        :meth:`.Flask.add_template_global` 。

        :param f: 注册的函数。
        :param name: 将全局注册为的名称。如果未给出，则使用函数的名称。

        .. versionadded:: 0.10
        """

        def register_template_global(state: BlueprintSetupState) -> None:
            state.app.add_template_global(f, name=name)

        self.record_once(register_template_global)

    @setupmethod
    def before_app_request(self, f: T_before_request) -> T_before_request:
        """与 :meth:`before_request` 类似，但在每个请求之前，而不仅仅是那些由蓝图处理的
        请求之前。相当于 :meth:`.Flask.before_request` 。
        """
        self.record_once(
            lambda s: s.app.before_request_funcs.setdefault(None, []).append(f)
        )
        return f

    @setupmethod
    def after_app_request(self, f: T_after_request) -> T_after_request:
        """与 :meth:`after_request` 类似，但在每个请求之后，而不仅仅是那些由蓝图处理的请
        求之后。相当于 :meth:`.Flask.after_request` 。
        """
        self.record_once(
            lambda s: s.app.after_request_funcs.setdefault(None, []).append(f)
        )
        return f

    @setupmethod
    def teardown_app_request(self, f: T_teardown) -> T_teardown:
        """与 :meth:`teardown_request` 类似，但在每个请求之后，而不仅仅是那些由蓝图处理
        的请求之后。相当于 :meth:`.Flask.teardown_request` 。
        """
        self.record_once(
            lambda s: s.app.teardown_request_funcs.setdefault(None, []).append(f)
        )
        return f

    @setupmethod
    def app_context_processor(
        self, f: T_template_context_processor
    ) -> T_template_context_processor:
        """与 :meth:`context_processor` 类似，但适用于每个视图渲染的模板，而不仅仅是蓝图
        。相当于 :meth:`.Flask.context_processor` 。
        """
        self.record_once(
            lambda s: s.app.template_context_processors.setdefault(None, []).append(f)
        )
        return f

    @setupmethod
    def app_errorhandler(
        self, code: type[Exception] | int
    ) -> t.Callable[[T_error_handler], T_error_handler]:
        """与 :meth:`errorhandler` 类似，但针对每个请求，而不仅仅是那些由蓝图处理的请求
        。相当于 :meth:`.Flask.errorhandler` 。
        """

        def decorator(f: T_error_handler) -> T_error_handler:
            def from_blueprint(state: BlueprintSetupState) -> None:
                state.app.errorhandler(code)(f)

            self.record_once(from_blueprint)
            return f

        return decorator

    @setupmethod
    def app_url_value_preprocessor(
        self, f: T_url_value_preprocessor
    ) -> T_url_value_preprocessor:
        """与 :meth:`url_value_preprocessor` 类似，但针对每个请求，而不仅仅是那些由蓝图
        处理的请求。相当于 :meth:`.Flask.url_value_preprocessor` 。
        """
        self.record_once(
            lambda s: s.app.url_value_preprocessors.setdefault(None, []).append(f)
        )
        return f

    @setupmethod
    def app_url_defaults(self, f: T_url_defaults) -> T_url_defaults:
        """与 :meth:`url_defaults` 类似，但针对每个请求，而不仅仅是那些由蓝图处理的请求
        。相当于 :meth:`.Flask.url_defaults` 。
        """
        self.record_once(
            lambda s: s.app.url_default_functions.setdefault(None, []).append(f)
        )
        return f
