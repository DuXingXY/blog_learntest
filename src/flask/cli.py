from __future__ import annotations

import ast
import collections.abc as cabc
import importlib.metadata
import inspect
import os
import platform
import re
import sys
import traceback
import typing as t
from functools import update_wrapper
from operator import itemgetter
from types import ModuleType

import click
from click.core import ParameterSource
from werkzeug import run_simple
from werkzeug.serving import is_running_from_reloader
from werkzeug.utils import import_string

from .globals import current_app
from .helpers import get_debug_flag
from .helpers import get_load_dotenv

if t.TYPE_CHECKING:
    import ssl

    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIApplication
    from _typeshed.wsgi import WSGIEnvironment

    from .app import Flask


class NoAppException(click.UsageError):
    """如果无法找到或加载应用程序，则会引发该异常。"""


def find_best_app(module: ModuleType) -> Flask:
    """给定一个模块实例，它会尝试在模块中找到最佳的应用程序，否则会引发异常。"""
    from . import Flask

    # 首先搜索最常见的名称。
    for attr_name in ("app", "application"):
        app = getattr(module, attr_name, None)

        if isinstance(app, Flask):
            return app

    # 否则找到唯一一个 Flask 实例的对象。
    matches = [v for v in module.__dict__.values() if isinstance(v, Flask)]

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        raise NoAppException(
            "Detected multiple Flask applications in module"
            f" '{module.__name__}'. Use '{module.__name__}:name'"
            " to specify the correct one."
        )

    # 搜索应用程序工厂功能。
    for attr_name in ("create_app", "make_app"):
        app_factory = getattr(module, attr_name, None)

        if inspect.isfunction(app_factory):
            try:
                app = app_factory()

                if isinstance(app, Flask):
                    return app
            except TypeError as e:
                if not _called_with_wrong_args(app_factory):
                    raise

                raise NoAppException(
                    f"Detected factory '{attr_name}' in module '{module.__name__}',"
                    " but could not call it without arguments. Use"
                    f" '{module.__name__}:{attr_name}(args)'"
                    " to specify arguments."
                ) from e

    raise NoAppException(
        "Failed to find Flask application or factory in module"
        f" '{module.__name__}'. Use '{module.__name__}:name'"
        " to specify one."
    )


def _called_with_wrong_args(f: t.Callable[..., Flask]) -> bool:
    """检查调用函数是否因调用失败或工厂中的某些内容引发错误而引发 ``TypeError`` 。

    :param f: 被调用的函数。
    :return: ``True`` 如果调用失败。
    """
    tb = sys.exc_info()[2]

    try:
        while tb is not None:
            if tb.tb_frame.f_code is f.__code__:
                # 在函数中，调用成功。
                return False

            tb = tb.tb_next

        # 没达到功能。
        return True
    finally:
        # 删除 tb 以打破循环引用。
        # https://docs.python.org/2/library/sys.html#sys.exc_info
        del tb


def find_app_by_string(module: ModuleType, app_name: str) -> Flask:
    """检查给定的字符串是否是变量名或函数。调用函数获取app实例，或者直接返回变量。"""
    from . import Flask

    # 将 app_name 解析为单个表达式，以确定它是否是有效的属性名称或函数调用。
    try:
        expr = ast.parse(app_name.strip(), mode="eval").body
    except SyntaxError:
        raise NoAppException(
            f"Failed to parse {app_name!r} as an attribute name or function call."
        ) from None

    if isinstance(expr, ast.Name):
        name = expr.id
        args = []
        kwargs = {}
    elif isinstance(expr, ast.Call):
        # 确保函数名称只是属性名称。
        if not isinstance(expr.func, ast.Name):
            raise NoAppException(
                f"Function reference must be a simple name: {app_name!r}."
            )

        name = expr.func.id

        # 将位置参数和关键字参数解析为文字。
        try:
            args = [ast.literal_eval(arg) for arg in expr.args]
            kwargs = {
                kw.arg: ast.literal_eval(kw.value)
                for kw in expr.keywords
                if kw.arg is not None
            }
        except ValueError:
            # literal_eval 给出神秘的错误消息，而是显示带有完整表达式的通用消息。
            raise NoAppException(
                f"Failed to parse arguments as literal values: {app_name!r}."
            ) from None
    else:
        raise NoAppException(
            f"Failed to parse {app_name!r} as an attribute name or function call."
        )

    try:
        attr = getattr(module, name)
    except AttributeError as e:
        raise NoAppException(
            f"Failed to find attribute {name!r} in {module.__name__!r}."
        ) from e

    # 如果该属性是一个函数，请使用任何 args 和 kwargs 调用它以获得真正的应用程序。
    if inspect.isfunction(attr):
        try:
            app = attr(*args, **kwargs)
        except TypeError as e:
            if not _called_with_wrong_args(attr):
                raise

            raise NoAppException(
                f"The factory {app_name!r} in module"
                f" {module.__name__!r} could not be called with the"
                " specified arguments."
            ) from e
    else:
        app = attr

    if isinstance(app, Flask):
        return app

    raise NoAppException(
        "A valid Flask application was not obtained from"
        f" '{module.__name__}:{app_name}'."
    )


def prepare_import(path: str) -> str:
    """给定一个文件名，这将尝试计算 python 路径，将其添加到搜索路径并返回所需的实际模块
    名称。
    """
    path = os.path.realpath(path)

    fname, ext = os.path.splitext(path)
    if ext == ".py":
        path = fname

    if os.path.basename(path) == "__init__":
        path = os.path.dirname(path)

    module_name = []

    # 向上移动直到外部包结构（无 __init__.py）
    while True:
        path, name = os.path.split(path)
        module_name.append(name)

        if not os.path.exists(os.path.join(path, "__init__.py")):
            break

    if sys.path[0] != path:
        sys.path.insert(0, path)

    return ".".join(module_name[::-1])


@t.overload
def locate_app(
    module_name: str, app_name: str | None, raise_if_not_found: t.Literal[True] = True
) -> Flask: ...


@t.overload
def locate_app(
    module_name: str, app_name: str | None, raise_if_not_found: t.Literal[False] = ...
) -> Flask | None: ...


def locate_app(
    module_name: str, app_name: str | None, raise_if_not_found: bool = True
) -> Flask | None:
    try:
        __import__(module_name)
    except ImportError:
        # 如果 ImportError 发生在导入的模块中，则重新引发该错误。通过检查迹线的深度是否
        # > 1 来确定这一点。
        if sys.exc_info()[2].tb_next:  # type: ignore[union-attr]
            raise NoAppException(
                f"While importing {module_name!r}, an ImportError was"
                f" raised:\n\n{traceback.format_exc()}"
            ) from None
        elif raise_if_not_found:
            raise NoAppException(f"Could not import {module_name!r}.") from None
        else:
            return None

    module = sys.modules[module_name]

    if app_name is None:
        return find_best_app(module)
    else:
        return find_app_by_string(module, app_name)


def get_version(ctx: click.Context, param: click.Parameter, value: t.Any) -> None:
    if not value or ctx.resilient_parsing:
        return

    flask_version = importlib.metadata.version("flask")
    werkzeug_version = importlib.metadata.version("werkzeug")

    click.echo(
        f"Python {platform.python_version()}\n"
        f"Flask {flask_version}\n"
        f"Werkzeug {werkzeug_version}",
        color=ctx.color,
    )
    ctx.exit()


version_option = click.Option(
    ["--version"],
    help="Show the Flask version.",
    expose_value=False,
    callback=get_version,
    is_flag=True,
    is_eager=True,
)


class ScriptInfo:
    """处理 Flask 应用程序的帮助程序对象。  这通常不需要进行交互，因为它在点击调度中内部
    使用。  在 Flask 的未来版本中，该对象很可能会发挥更大的作用。  通常它是由
    :class:`FlaskGroup` 自动创建的，但您也可以手动创建它并将其作为单击对象传递。

    .. versionchanged:: 3.1
        添加了 ``load_dotenv_defaults`` 参数和属性。
    """

    def __init__(
        self,
        app_import_path: str | None = None,
        create_app: t.Callable[..., Flask] | None = None,
        set_debug_flag: bool = True,
        load_dotenv_defaults: bool = True,
    ) -> None:
        #: （可选）Flask 应用程序的导入路径。
        self.app_import_path = app_import_path
        #: （可选）传递脚本信息以创建应用程序实例的函数。
        self.create_app = create_app
        #: 包含可以与此脚本信息关联的任意数据的字典。
        self.data: dict[t.Any, t.Any] = {}
        self.set_debug_flag = set_debug_flag

        self.load_dotenv_defaults = get_load_dotenv(load_dotenv_defaults)
        """是否应加载默认的 ``.flaskenv`` 和 ``.env`` 文件。

        ``ScriptInfo`` 不加载任何内容，这供在处理过程中在其他地方进行加载时参考。

        .. versionadded:: 3.1
        """

        self._loaded_app: Flask | None = None

    def load_app(self) -> Flask:
        """加载 Flask 应用程序（如果尚未加载）并返回它。  多次调用只会导致返回已加载的应
        用程序。
        """
        if self._loaded_app is not None:
            return self._loaded_app
        app: Flask | None = None
        if self.create_app is not None:
            app = self.create_app()
        else:
            if self.app_import_path:
                path, name = (
                    re.split(r":(?![\\/])", self.app_import_path, maxsplit=1) + [None]
                )[:2]
                import_name = prepare_import(path)
                app = locate_app(import_name, name)
            else:
                for path in ("wsgi.py", "app.py"):
                    import_name = prepare_import(path)
                    app = locate_app(import_name, None, raise_if_not_found=False)

                    if app is not None:
                        break

        if app is None:
            raise NoAppException(
                "Could not locate a Flask application. Use the"
                " 'flask --app' option, 'FLASK_APP' environment"
                " variable, or a 'wsgi.py' or 'app.py' file in the"
                " current directory."
            )

        if self.set_debug_flag:
            # 通过描述符更新应用程序的调试标志，以便其他值也重新填充。
            app.debug = get_debug_flag()

        self._loaded_app = app
        return app


pass_script_info = click.make_pass_decorator(ScriptInfo, ensure=True)

F = t.TypeVar("F", bound=t.Callable[..., t.Any])


def with_appcontext(f: F) -> F:
    """包装回调，以便保证它在脚本的应用程序上下文中执行。

    在 ``app.cli`` 或 ``blueprint.cli`` 下注册的自定义命令（及其选项）将始终具有可用的应
    用程序上下文，在这种情况下不需要此装饰器。

    .. versionchanged:: 2.2
        应用程序上下文对于子命令以及修饰回调是活动的。应用程序上下文始终可用于
        ``app.cli`` 命令和参数回调。
    """

    @click.pass_context
    def decorator(ctx: click.Context, /, *args: t.Any, **kwargs: t.Any) -> t.Any:
        if not current_app:
            app = ctx.ensure_object(ScriptInfo).load_app()
            ctx.with_resource(app.app_context())

        return ctx.invoke(f, *args, **kwargs)

    return update_wrapper(decorator, f)  # type: ignore[return-value]


class AppGroup(click.Group):
    """这与常规单击 :class:`~click.Group` 类似，但它更改了 :meth:`command` 装饰器的行为
    ，以便它自动将函数包装在 :func:`with_appcontext` 中。

    不要与 :class:`FlaskGroup` 混淆。
    """

    def command(  # type: ignore[override]
        self, *args: t.Any, **kwargs: t.Any
    ) -> t.Callable[[t.Callable[..., t.Any]], click.Command]:
        """这与常规 :class:`click.Group` 上的同名方法完全相同，但它将回调包装在
        :func:`with_appcontext` 中，除非通过传递 ``with_appcontext=False`` 来禁用它。
        """
        wrap_for_ctx = kwargs.pop("with_appcontext", True)

        def decorator(f: t.Callable[..., t.Any]) -> click.Command:
            if wrap_for_ctx:
                f = with_appcontext(f)
            return super(AppGroup, self).command(*args, **kwargs)(f)  # type: ignore[no-any-return]

        return decorator

    def group(  # type: ignore[override]
        self, *args: t.Any, **kwargs: t.Any
    ) -> t.Callable[[t.Callable[..., t.Any]], click.Group]:
        """这与常规 :class:`click.Group` 上的同名方法完全相同，但它默认组类为
        :class:`AppGroup` 。
        """
        kwargs.setdefault("cls", AppGroup)
        return super().group(*args, **kwargs)  # type: ignore[no-any-return]


def _set_app(ctx: click.Context, param: click.Option, value: str | None) -> str | None:
    if value is None:
        return None

    info = ctx.ensure_object(ScriptInfo)
    info.app_import_path = value
    return value


# 此选项是急切的，因此如果给出 --help ，应用程序将可用。 --help 也是急切的，所以 --app
# 必须位于参数列表中的前面。 no_args_is_help 绕过急切处理，因此在这种情况下必须手动处理
# 此选项，以确保 FLASK_APP 被选中。
_app_option = click.Option(
    ["-A", "--app"],
    metavar="IMPORT",
    help=(
        "The Flask application or factory function to load, in the form 'module:name'."
        " Module can be a dotted import or file path. Name is not required if it is"
        " 'app', 'application', 'create_app', or 'make_app', and can be 'name(args)' to"
        " pass arguments."
    ),
    is_eager=True,
    expose_value=False,
    callback=_set_app,
)


def _set_debug(ctx: click.Context, param: click.Option, value: bool) -> bool | None:
    # 如果未提供该标志，则默认为 False。不要使用它，在这种情况下让 debug 由 env 设置。
    source = ctx.get_parameter_source(param.name)

    if source is not None and source in (
        ParameterSource.DEFAULT,
        ParameterSource.DEFAULT_MAP,
    ):
        return None

    # 使用 env var 而不是 ScriptInfo.load 设置，以便可以在工厂函数期间尽早访问它。
    os.environ["FLASK_DEBUG"] = "1" if value else "0"
    return value


_debug_option = click.Option(
    ["--debug/--no-debug"],
    help="Set debug mode.",
    expose_value=False,
    callback=_set_debug,
)


def _env_file_callback(
    ctx: click.Context, param: click.Option, value: str | None
) -> str | None:
    try:
        import dotenv  # noqa: F401
    except ImportError:
        # 仅当传递值时才显示错误，否则我们仍然要调用 load_dotenv 并显示消息而不退出。
        if value is not None:
            raise click.BadParameter(
                "python-dotenv must be installed to load an env file.",
                ctx=ctx,
                param=param,
            ) from None

    # 如果传递了值，或者我们想要加载默认文件，或者两者都加载，则加载。
    if value is not None or ctx.obj.load_dotenv_defaults:
        load_dotenv(value, load_defaults=ctx.obj.load_dotenv_defaults)

    return value


# 此选项是急切的，因此环境变量会尽早加载以供其他选项使用。
_env_file_option = click.Option(
    ["-e", "--env-file"],
    type=click.Path(exists=True, dir_okay=False),
    help=(
        "Load environment variables from this file, taking precedence over"
        " those set by '.env' and '.flaskenv'. Variables set directly in the"
        " environment take highest precedence. python-dotenv must be installed."
    ),
    is_eager=True,
    expose_value=False,
    callback=_env_file_callback,
)


class FlaskGroup(AppGroup):
    """:class:`AppGroup` 组的特殊子类，支持从配置的 Flask 应用程序加载更多命令。  通常，
    开发人员不必与此类交互，但有一些非常高级的用例，创建此类的实例是有意义的。参见
    :ref:`custom-scripts` 。

    :param add_default_commands: 如果这是 True 那么将添加默认的运行和 shell 命令。
    :param add_version_option: 添加 ``--version`` 选项。
    :param create_app: 一个可选的回调，传递脚本信息并返回加载的应用程序。
    :param load_dotenv: 加载最近的 :file:`.env` 和 :file:`.flaskenv` 文件来设置环境变量
        。还将工作目录更改为包含找到的第一个文件的目录。
    :param set_debug_flag: 设置应用程序的调试标志。

    .. versionchanged:: 3.1
        ``-e path`` 优先于默认的 ``.env`` 和 ``.flaskenv`` 文件。

    .. versionchanged:: 2.2
        添加了 ``-A/--app`` 、 ``--debug/--no-debug`` 、 ``-e/--env-file`` 选项。

    .. versionchanged:: 2.2
        运行 ``app.cli`` 命令时会推送应用程序上下文，因此这些命令不再需要
        ``@with_appcontext`` 。

    .. versionchanged:: 1.0
        如果安装，python-dotenv 将用于从 :file:`.env` 和 :file:`.flaskenv` 文件加载环境
        变量。
    """

    def __init__(
        self,
        add_default_commands: bool = True,
        create_app: t.Callable[..., Flask] | None = None,
        add_version_option: bool = True,
        load_dotenv: bool = True,
        set_debug_flag: bool = True,
        **extra: t.Any,
    ) -> None:
        params: list[click.Parameter] = list(extra.pop("params", None) or ())
        # 处理是通过选项回调而不是组回调完成的。这允许用户进行自定义组回调而不会丢失行为
        # 。 --env-file 必须先出现，以便在 --app 之前对其进行急切评估。
        params.extend((_env_file_option, _app_option, _debug_option))

        if add_version_option:
            params.append(version_option)

        if "context_settings" not in extra:
            extra["context_settings"] = {}

        extra["context_settings"].setdefault("auto_envvar_prefix", "FLASK")

        super().__init__(params=params, **extra)

        self.create_app = create_app
        self.load_dotenv = load_dotenv
        self.set_debug_flag = set_debug_flag

        if add_default_commands:
            self.add_command(run_command)
            self.add_command(shell_command)
            self.add_command(routes_command)

        self._loaded_plugin_commands = False

    def _load_plugin_commands(self) -> None:
        if self._loaded_plugin_commands:
            return

        for ep in importlib.metadata.entry_points(group="flask.commands"):
            self.add_command(ep.load(), ep.name)

        self._loaded_plugin_commands = True

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        self._load_plugin_commands()
        # 查找内置命令和插件命令，即使应用程序无法加载，这些命令也应该可用。
        rv = super().get_command(ctx, name)

        if rv is not None:
            return rv

        info = ctx.ensure_object(ScriptInfo)

        # 查找应用程序提供的命令，显示错误，如果无法加载应用程序则继续。
        try:
            app = info.load_app()
        except NoAppException as e:
            click.secho(f"Error: {e.format_message()}\n", err=True, fg="red")
            return None

        # 推送已加载应用程序的应用程序上下文，除非它已经以某种方式处于活动状态。这使得上
        # 下文可用于参数和命令回调，而无需@with_appcontext。
        if not current_app or current_app._get_current_object() is not app:
            ctx.with_resource(app.app_context())

        return app.cli.get_command(ctx, name)

    def list_commands(self, ctx: click.Context) -> list[str]:
        self._load_plugin_commands()
        # 从内置命令和插件命令开始。
        rv = set(super().list_commands(ctx))
        info = ctx.ensure_object(ScriptInfo)

        # 添加应用程序提供的命令，如果无法加载应用程序，则显示错误并继续。
        try:
            rv.update(info.load_app().cli.list_commands(ctx))
        except NoAppException as e:
            # 当应用程序无法加载时，显示错误消息而不进行回溯。
            click.secho(f"Error: {e.format_message()}\n", err=True, fg="red")
        except Exception:
            # 当加载过程中发生任何其他错误时，显示完整的回溯。
            click.secho(f"{traceback.format_exc()}\n", err=True, fg="red")

        return sorted(rv)

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: t.Any,
    ) -> click.Context:
        # 设置一个标志来告诉 app.run 成为无操作。如果 app.run 不在 __name__ == __main__
        # 保护中，它将在导入时启动服务器，阻止正在调用的任何命令。
        os.environ["FLASK_RUN_FROM_CLI"] = "true"

        if "obj" not in extra and "obj" not in self.context_settings:
            extra["obj"] = ScriptInfo(
                create_app=self.create_app,
                set_debug_flag=self.set_debug_flag,
                load_dotenv_defaults=self.load_dotenv,
            )

        return super().make_context(info_name, args, parent=parent, **extra)

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if (not args and self.no_args_is_help) or (
            len(args) == 1 and args[0] in self.get_help_option_names(ctx)
        ):
            # 尝试尽早加载 --env-file 和 --app，以防它们作为环境变量给出。否则
            # no_args_is_help 将看不到来自 app.cli 的命令。
            _env_file_option.handle_parse_result(ctx, {}, [])
            _app_option.handle_parse_result(ctx, {}, [])

        return super().parse_args(ctx, args)


def _path_is_ancestor(path: str, other: str) -> bool:
    """获取 ``other`` 并从中删除 ``path`` 的长度。然后将其加入 ``path`` 。如果是原始值，
    则 ``path`` 是 ``other`` 的祖先。"""
    return os.path.join(path, other[len(path) :].lstrip(os.sep)) == other


def load_dotenv(
    path: str | os.PathLike[str] | None = None, load_defaults: bool = True
) -> bool:
    """加载 "dotenv" 文件以设置环境变量。给定路径优先于 ``.env`` ，而 ``.env`` 又优先于
    ``.flaskenv`` 。加载并组合这些文件后，只有在 ``os.environ`` 中尚未设置密钥时才会设置
    值。

    如果未安装 `python-dotenv`_ ，则此操作无效。

    .. _python-dotenv: https://github.com/theskumar/python-dotenv#readme

    :param path: 在此位置加载文件。
    :param load_defaults: 搜索并加载默认的 ``.flaskenv`` 和 ``.env`` 文件。
    :return: ``True`` 如果至少加载了一个环境变量。

    .. versionchanged:: 3.1
        添加了 ``load_defaults`` 参数。给定路径优先于默认文件。

    .. versionchanged:: 2.0
        当前目录不会更改为加载文件的位置。

    .. versionchanged:: 2.0
        加载 env 文件时，将默认编码设置为 UTF-8。

    .. versionchanged:: 1.1.0
        当未安装 python-dotenv 或给定路径不是文件时，返回 ``False`` 。

    .. versionadded:: 1.0
    """
    try:
        import dotenv
    except ImportError:
        if path or os.path.isfile(".env") or os.path.isfile(".flaskenv"):
            click.secho(
                " * Tip: There are .env files present. Install python-dotenv"
                " to use them.",
                fg="yellow",
                err=True,
            )

        return False

    data: dict[str, str | None] = {}

    if load_defaults:
        for default_name in (".flaskenv", ".env"):
            if not (default_path := dotenv.find_dotenv(default_name, usecwd=True)):
                continue

            data |= dotenv.dotenv_values(default_path, encoding="utf-8")

    if path is not None and os.path.isfile(path):
        data |= dotenv.dotenv_values(path, encoding="utf-8")

    for key, value in data.items():
        if key in os.environ or value is None:
            continue

        os.environ[key] = value

    return bool(data)  # True 如果至少加载了一个环境变量。


def show_server_banner(debug: bool, app_import_path: str | None) -> None:
    """服务器第一次运行时显示额外的启动消息，忽略重新加载器。"""
    if is_running_from_reloader():
        return

    if app_import_path is not None:
        click.echo(f" * Serving Flask app '{app_import_path}'")

    if debug is not None:
        click.echo(f" * Debug mode: {'on' if debug else 'off'}")


class CertParamType(click.ParamType):  # type: ignore[type-arg]
    """``--cert`` 选项的 Click 选项类型。允许现有文件、字符串 ``'adhoc'`` 或导入
    :class:`~ssl.SSLContext` 对象。
    """

    name = "path"

    def __init__(self) -> None:
        self.path_type = click.Path(exists=True, dir_okay=False, resolve_path=True)

    def convert(
        self, value: t.Any, param: click.Parameter | None, ctx: click.Context | None
    ) -> t.Any:
        try:
            import ssl
        except ImportError:
            raise click.BadParameter(
                'Using "--cert" requires Python to be compiled with SSL support.',
                ctx,
                param,
            ) from None

        try:
            return self.path_type(value, param, ctx)
        except click.BadParameter:
            value = click.STRING(value, param, ctx).lower()  # type: ignore[union-attr]

            if value == "adhoc":
                try:
                    import cryptography  # noqa: F401
                except ImportError:
                    raise click.BadParameter(
                        "Using ad-hoc certificates requires the cryptography library.",
                        ctx,
                        param,
                    ) from None

                return value

            obj = import_string(value, silent=True)

            if isinstance(obj, ssl.SSLContext):
                return obj

            raise


def _validate_key(ctx: click.Context, param: click.Parameter, value: t.Any) -> t.Any:
    """当 ``--cert`` 是文件时，必须指定 ``--key`` 选项。如果需要，将 ``cert`` 参数修改为
    ``(cert, key)`` 对。
    """
    cert = ctx.params.get("cert")
    is_adhoc = cert == "adhoc"

    try:
        import ssl
    except ImportError:
        is_context = False
    else:
        is_context = isinstance(cert, ssl.SSLContext)

    if value is not None:
        if is_adhoc:
            raise click.BadParameter(
                'When "--cert" is "adhoc", "--key" is not used.', ctx, param
            )

        if is_context:
            raise click.BadParameter(
                'When "--cert" is an SSLContext object, "--key" is not used.',
                ctx,
                param,
            )

        if not cert:
            raise click.BadParameter('"--cert" must also be specified.', ctx, param)

        ctx.params["cert"] = cert, value

    else:
        if cert and not (is_adhoc or is_context):
            raise click.BadParameter('Required when using "--cert".', ctx, param)

    return value


class SeparatedPathType(click.Path):
    """Click 选项类型，接受由 OS 的路径分隔符分隔的值列表（Windows 上为 ``:`` 、 ``;``
    ）。每个值都被验证为 :class:`click.Path` 类型。
    """

    def convert(
        self, value: t.Any, param: click.Parameter | None, ctx: click.Context | None
    ) -> t.Any:
        items = self.split_envvar_value(value)
        super_convert = super().convert
        return [super_convert(item, param, ctx) for item in items]


@click.command("run", short_help="Run a development server.")
@click.option("--host", "-h", default="127.0.0.1", help="The interface to bind to.")
@click.option("--port", "-p", default=5000, help="The port to bind to.")
@click.option(
    "--cert",
    type=CertParamType(),
    help="Specify a certificate file to use HTTPS.",
    is_eager=True,
)
@click.option(
    "--key",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    callback=_validate_key,
    expose_value=False,
    help="The key file to use when specifying a certificate.",
)
@click.option(
    "--reload/--no-reload",
    default=None,
    help="Enable or disable the reloader. By default the reloader "
    "is active if debug is enabled.",
)
@click.option(
    "--debugger/--no-debugger",
    default=None,
    help="Enable or disable the debugger. By default the debugger "
    "is active if debug is enabled.",
)
@click.option(
    "--with-threads/--without-threads",
    default=True,
    help="Enable or disable multithreading.",
)
@click.option(
    "--extra-files",
    default=None,
    type=SeparatedPathType(),
    help=(
        "Extra files that trigger a reload on change. Multiple paths"
        f" are separated by {os.path.pathsep!r}."
    ),
)
@click.option(
    "--exclude-patterns",
    default=None,
    type=SeparatedPathType(),
    help=(
        "Files matching these fnmatch patterns will not trigger a reload"
        " on change. Multiple patterns are separated by"
        f" {os.path.pathsep!r}."
    ),
)
@pass_script_info
def run_command(
    info: ScriptInfo,
    host: str,
    port: int,
    reload: bool,
    debugger: bool,
    with_threads: bool,
    cert: ssl.SSLContext | tuple[str, str | None] | t.Literal["adhoc"] | None,
    extra_files: list[str] | None,
    exclude_patterns: list[str] | None,
) -> None:
    """运行本地开发服务器。

    该服务器仅用于开发目的。它不提供生产 WSGI 服务器的稳定性、安全性或性能。

    默认情况下，使用 '--debug' 选项启用重新加载器和调试器。
    """
    try:
        app: WSGIApplication = info.load_app()  # pyright: ignore
    except Exception as e:
        if is_running_from_reloader():
            # 重新加载时，立即打印出错误，但稍后提出它，以便调试器或服务器可以处理它。
            traceback.print_exc()
            err = e

            def app(
                environ: WSGIEnvironment, start_response: StartResponse
            ) -> cabc.Iterable[bytes]:
                raise err from None

        else:
            # 不重新加载时，立即引发错误，以便命令失败。
            raise e from None

    debug = get_debug_flag()

    if reload is None:
        reload = debug

    if debugger is None:
        debugger = debug

    show_server_banner(debug, info.app_import_path)

    run_simple(
        host,
        port,
        app,
        use_reloader=reload,
        use_debugger=debugger,
        threaded=with_threads,
        ssl_context=cert,
        extra_files=extra_files,
        exclude_patterns=exclude_patterns,
    )


run_command.params.insert(0, _debug_option)


@click.command("shell", short_help="Run a shell in the app context.")
@with_appcontext
def shell_command() -> None:
    """在给定 Flask 应用程序的上下文中运行交互式 Python shell。  应用程序将根据其配置填
    充此 shell 的默认命名空间。

    这对于执行管理代码的小片段非常有用，而无需手动配置应用程序。
    """
    import code

    banner = (
        f"Python {sys.version} on {sys.platform}\n"
        f"App: {current_app.import_name}\n"
        f"Instance: {current_app.instance_path}"
    )
    ctx: dict[str, t.Any] = {}

    # 如果有人使用常规 Python 解释器启动脚本，则支持它。
    startup = os.environ.get("PYTHONSTARTUP")
    if startup and os.path.isfile(startup):
        with open(startup) as f:
            eval(compile(f.read(), startup, "exec"), ctx)

    ctx.update(current_app.make_shell_context())

    # 站点、自定义或启动脚本可以设置进入交互模式时调用的钩子。默认设置带有选项卡和历史记
    # 录补全功能的读取行。
    interactive_hook = getattr(sys, "__interactivehook__", None)

    if interactive_hook is not None:
        try:
            import readline
            from rlcompleter import Completer
        except ImportError:
            pass
        else:
            # rlcompleter 默认使用 __main__.__dict__，即 flask.__main__。请改用 shell 上
            # 下文。
            readline.set_completer(Completer(ctx).complete)

        interactive_hook()

    code.interact(banner=banner, local=ctx)


@click.command("routes", short_help="Show the routes for the app.")
@click.option(
    "--sort",
    "-s",
    type=click.Choice(("endpoint", "methods", "domain", "rule", "match")),
    default="endpoint",
    help=(
        "Method to sort routes by. 'match' is the order that Flask will match routes"
        " when dispatching a request."
    ),
)
@click.option("--all-methods", is_flag=True, help="Show HEAD and OPTIONS methods.")
@with_appcontext
def routes_command(sort: str, all_methods: bool) -> None:
    """显示所有已注册的路由以及端点和方法。"""
    rules = list(current_app.url_map.iter_rules())

    if not rules:
        click.echo("No routes were registered.")
        return

    ignored_methods = set() if all_methods else {"HEAD", "OPTIONS"}
    host_matching = current_app.url_map.host_matching
    has_domain = any(rule.host if host_matching else rule.subdomain for rule in rules)
    rows = []

    for rule in rules:
        row = [
            rule.endpoint,
            ", ".join(sorted((rule.methods or set()) - ignored_methods)),
        ]

        if has_domain:
            row.append((rule.host if host_matching else rule.subdomain) or "")

        row.append(rule.rule)
        rows.append(row)

    headers = ["Endpoint", "Methods"]
    sorts = ["endpoint", "methods"]

    if has_domain:
        headers.append("Host" if host_matching else "Subdomain")
        sorts.append("domain")

    headers.append("Rule")
    sorts.append("rule")

    try:
        rows.sort(key=itemgetter(sorts.index(sort)))
    except ValueError:
        pass

    rows.insert(0, headers)
    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
    rows.insert(1, ["-" * w for w in widths])
    template = "  ".join(f"{{{i}:<{w}}}" for i, w in enumerate(widths))

    for row in rows:
        click.echo(template.format(*row))


cli = FlaskGroup(
    name="flask",
    help="""\
A general utility script for Flask applications.

An application to load must be given with the '--app' option,
'FLASK_APP' environment variable, or with a 'wsgi.py' or 'app.py' file
in the current directory.
""",
)


def main() -> None:
    cli.main()


if __name__ == "__main__":
    main()
