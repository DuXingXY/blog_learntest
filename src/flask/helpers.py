from __future__ import annotations

import importlib.util
import os
import sys
import typing as t
from datetime import datetime
from functools import cache
from functools import update_wrapper
from types import TracebackType

import werkzeug.utils
from werkzeug.exceptions import abort as _wz_abort
from werkzeug.utils import redirect as _wz_redirect
from werkzeug.wrappers import Response as BaseResponse

from .globals import _cv_app
from .globals import app_ctx
from .globals import current_app
from .globals import request
from .globals import session
from .signals import message_flashed

if t.TYPE_CHECKING:  # pragma: no cover
    from .wrappers import Response


def get_debug_flag() -> bool:
    """获取是否应为应用程序启用调试模式，由 :envvar:`FLASK_DEBUG` 环境变量指示。默认为
    ``False`` 。
    """
    val = os.environ.get("FLASK_DEBUG")
    return bool(val and val.lower() not in {"0", "false", "no"})


def get_load_dotenv(default: bool = True) -> bool:
    """通过设置 :envvar:`FLASK_SKIP_DOTENV` 获取用户是否禁用加载默认 dotenv 文件。默认为
    ``True`` ，加载文件。

    :param default: 如果未设置环境变量，则返回什么。
    """
    val = os.environ.get("FLASK_SKIP_DOTENV")

    if not val:
        return default

    return val.lower() in ("0", "false", "no")


@t.overload
def stream_with_context(
    generator_or_function: t.Iterator[t.AnyStr],
) -> t.Iterator[t.AnyStr]: ...


@t.overload
def stream_with_context(
    generator_or_function: t.Callable[..., t.Iterator[t.AnyStr]],
) -> t.Callable[[t.Iterator[t.AnyStr]], t.Iterator[t.AnyStr]]: ...


def stream_with_context(
    generator_or_function: t.Iterator[t.AnyStr] | t.Callable[..., t.Iterator[t.AnyStr]],
) -> t.Iterator[t.AnyStr] | t.Callable[[t.Iterator[t.AnyStr]], t.Iterator[t.AnyStr]]:
    """包装响应生成器函数，使其在当前请求上下文中运行。这使得 :data:`.request` 、
    :data:`.session` 和 :data:`.g` 保持可用，即使在生成器运行时请求上下文通常已经结束。

    .. warning::

        由于以下警告，将所需的数据作为参数传递给生成器通常比依赖上下文对象更安全。

    正文开始后无法发送更多标头。因此，您必须确保在开始响应之前设置所有标头。特别是，如果
    生成器将访问 ``session`` ，请确保也在视图中执行此操作，以便设置 ``Vary: cookie`` 标
    头。不要修改生成器中的会话，因为 ``Set-Cookie`` 标头已经被发送。

    将其用作生成器函数的装饰器：

    .. code-block:: python

        from flask import stream_with_context, request, Response

        @app.get("/stream")
        def streamed_response():
            @stream_with_context
            def generate():
                yield "Hello "
                yield request.args["name"]
                yield "!"

            return Response(generate())

    或者将其用作创建的生成器的包装器：

    .. code-block:: python

        from flask import stream_with_context, request, Response

        @app.get("/stream")
        def streamed_response():
            def generate():
                yield "Hello "
                yield request.args["name"]
                yield "!"

            return Response(stream_with_context(generate()))

    .. versionadded:: 0.9
    """
    try:
        gen = iter(generator_or_function)  # type: ignore[arg-type]
    except TypeError:

        def decorator(*args: t.Any, **kwargs: t.Any) -> t.Any:
            gen = generator_or_function(*args, **kwargs)  # type: ignore[operator]
            return stream_with_context(gen)

        return update_wrapper(decorator, generator_or_function)  # type: ignore[arg-type]

    def generator() -> t.Iterator[t.AnyStr]:
        if (ctx := _cv_app.get(None)) is None:
            raise RuntimeError(
                "'stream_with_context' can only be used when a request"
                " context is active, such as in a view function."
            )

        with ctx:
            yield None  # type: ignore[misc]

            try:
                yield from gen
            finally:
                # 清理以防用户包装 WSGI 迭代器。
                if hasattr(gen, "close"):
                    gen.close()

    # 将生成器执行到哨兵值。这会捕获当前上下文并推送它以保留它。进一步的迭代将从原始迭代
    # 器中产生。
    wrapped_g = generator()
    next(wrapped_g)
    return wrapped_g


def make_response(*args: t.Any) -> Response:
    """有时需要在视图中设置额外的标题。  因为视图不必返回响应对象，但可以返回由 Flask 本
    身转换为响应对象的值，因此向其添加标头变得很棘手。  可以调用此函数而不是使用返回，您
    将获得一个可用于附加标头的响应对象。

    如果视图看起来像这样并且您想添加新标题::

        def index():
            return render_template('index.html', foo=42)

    你现在可以做这样的事情::

        def index():
            response = make_response(render_template('index.html', foo=42))
            response.headers['X-Parachutes'] = 'parachutes are cool'
            return response

    该函数接受与从视图函数返回的参数完全相同的参数。  例如，这会创建一个带有 404 错误代
    码的响应::

        response = make_response(render_template('not_found.html'), 404)

    该函数的另一个用例是强制将视图函数的返回值转换为响应，这对视图装饰器很有帮助::

        response = make_response(view_function())
        response.headers['X-Parachutes'] = 'parachutes are cool'

    该函数在内部执行以下操作：

    -   如果没有传递参数，它将创建一个新的响应参数
    -   如果传递一个参数，则用它调用 :meth:`flask.Flask.make_response` 。
    -   如果传递多个参数，这些参数将作为元组传递给 :meth:`flask.Flask.make_response` 函
        数。

    .. versionadded:: 0.6
    """
    if not args:
        return current_app.response_class()
    if len(args) == 1:
        args = args[0]
    return current_app.make_response(args)


def url_for(
    endpoint: str,
    *,
    _anchor: str | None = None,
    _method: str | None = None,
    _scheme: str | None = None,
    _external: bool | None = None,
    **values: t.Any,
) -> str:
    """使用给定值生成给定端点的 URL 。

    这需要一个活动请求或应用程序上下文，并调用
    :meth:`current_app.url_for() <flask.Flask.url_for>` 。请参阅该方法以获取完整文档。

    :param endpoint: 与要生成的 URL 关联的端点名称。如果以 ``.`` 开头，则将使用当前的蓝
        图名称（如果有）。
    :param _anchor: 如果给出，请将其作为 ``#anchor`` 附加到 URL 中。
    :param _method: 如果给定，则为端点生成与此方法关联的 URL。
    :param _scheme: 如果给定，URL 将具有此方案（如果它是外部的）。
    :param _external: 如果给定，则首选 URL 为内部 (False) 或要求它为外部 (True)。外部
        URL 包括方案和域。当不在活动请求中时，URL 默认是外部的。
    :param values: 用于 URL 规则的变量部分的值。未知键作为查询字符串参数附加，例如
        ``?a=b&c=d`` 。

    .. versionchanged:: 2.2
        调用 ``current_app.url_for`` ，允许应用程序覆盖该行为。

    .. versionchanged:: 0.10
       添加了 ``_scheme`` 参数。

    .. versionchanged:: 0.9
       添加了 ``_anchor`` 和 ``_method`` 参数。

    .. versionchanged:: 0.9
       在构建错误时调用 ``app.handle_url_build_error`` 。
    """
    return current_app.url_for(
        endpoint,
        _anchor=_anchor,
        _method=_method,
        _scheme=_scheme,
        _external=_external,
        **values,
    )


def redirect(
    location: str, code: int = 303, Response: type[BaseResponse] | None = None
) -> BaseResponse:
    """创建重定向响应对象。

    如果 :data:`~flask.current_app` 可用，它将使用其 :meth:`~flask.Flask.redirect` 方法
    ，否则将使用 :func:`werkzeug.utils.redirect` 。

    :param location: 要重定向到的 URL。
    :param code: 重定向的状态代码。
    :param Response: 要使用的响应类。当 ``current_app`` 处于活动状态时不使用，它使用
        ``app.response_class`` 。

    .. versionchanged:: 3.2
        ``code`` 默认为 ``303`` 而不是 ``302`` 。

    .. versionadded:: 2.2
        如果可用，则调用 ``current_app.redirect`` ，而不是始终使用 Werkzeug 的默认
        ``redirect`` 。
    """
    if (ctx := _cv_app.get(None)) is not None:
        return ctx.app.redirect(location, code=code)

    return _wz_redirect(location, code=code, Response=Response)


def abort(code: int | BaseResponse, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
    """针对给定的状态代码引发 :exc:`~werkzeug.exceptions.HTTPException` 。

    如果 :data:`~flask.current_app` 可用，它将调用其 :attr:`~flask.Flask.aborter` 对象，
    否则将使用 :func:`werkzeug.exceptions.abort` 。

    :param code: 异常的状态码，必须注册在 ``app.aborter`` 中。
    :param args: 传递给异常。
    :param kwargs: 传递给异常。

    .. versionadded:: 2.2
        如果可用，则调用 ``current_app.aborter`` ，而不是始终使用 Werkzeug 的默认
        ``abort`` 。
    """
    if (ctx := _cv_app.get(None)) is not None:
        ctx.app.aborter(code, *args, **kwargs)

    _wz_abort(code, *args, **kwargs)


def get_template_attribute(template_name: str, attribute: str) -> t.Any:
    """加载模板导出的宏（或变量），以便在 Python 代码中调用宏。例如，名为
    :file:`_cider.html` 的模板包含以下内容：

    .. sourcecode:: html+jinja

       {% macro hello(name) %}Hello {{ name }}!{% endmacro %}

    您可以从 Python 代码访问它，如下所示::

        hello = get_template_attribute('_cider.html', 'hello')
        return hello('World')

    .. versionadded:: 0.2

    :param template_name: 模板的名称
    :param attribute: 要访问的宏变量的名称
    """
    return getattr(current_app.jinja_env.get_template(template_name).module, attribute)


def flash(message: str, category: str = "message") -> None:
    """将消息存入会话，供下一个请求显示。模板必须调用 :func:`get_flashed_messages` ，才
    能从会话中取出消息并显示给用户。

    .. versionchanged:: 0.3
       添加了 `category` 参数。

    :param message: 要显示的提示消息。
    :param category: 消息的类别。  建议使用以下值： ``'message'`` 用于任何类型的消息，
        ``'error'`` 用于错误， ``'info'`` 用于信息消息， ``'warning'`` 用于警告。  然而
        任何类型的字符串都可以用作类别。
    """
    # 原来的实现：
    #
    # session.setdefault('_flashes', []).append((类别, 消息))
    #
    # 这假设对会话中的可变结构所做的更改始终与会话对象同步，但对于使用外部存储来保存其键
    # /值的会话实现来说，情况并非如此。
    flashes = session.get("_flashes", [])
    flashes.append((category, message))
    session["_flashes"] = flashes
    app = current_app._get_current_object()
    message_flashed.send(
        app,
        _async_wrapper=app.ensure_sync,
        message=message,
        category=category,
    )


def get_flashed_messages(
    with_categories: bool = False, category_filter: t.Iterable[str] = ()
) -> list[str] | list[tuple[str, str]]:
    """从会话中提取所有闪现消息并返回它们。对函数的同一请求中的进一步调用将返回相同的消
    息。  默认情况下只返回消息，但是当 `with_categories` 设置为 ``True`` 时，返回值将是
    ``(category, message)`` 形式的元组列表。

    通过在 `category_filter` 中提供这些类别，将闪现的消息过滤到一个或多个类别。  这允许
    在单独的 html 块中呈现类别。  `with_categories` 和 `category_filter` 参数是不同的：

    * `with_categories` 控制类别是否与消息文本一起返回（ ``True`` 给出一个元组，其中
      ``False`` 仅给出消息文本）。
    * `category_filter` 将消息过滤为仅匹配所提供类别的消息。

    有关示例，请参阅 :doc:`/patterns/flashing` 。

    .. versionchanged:: 0.3
       添加了 `with_categories` 参数。

    .. versionchanged:: 0.9
        添加了 `category_filter` 参数。

    :param with_categories: 设置为 ``True`` 也可以接收类别。
    :param category_filter: 过滤类别以限制返回值。  仅返回列表中的类别。
    """
    flashes = app_ctx._flashes
    if flashes is None:
        flashes = session.pop("_flashes") if "_flashes" in session else []
        app_ctx._flashes = flashes
    if category_filter:
        flashes = list(filter(lambda f: f[0] in category_filter, flashes))
    if not with_categories:
        return [x[1] for x in flashes]
    return flashes


def _prepare_send_file_kwargs(**kwargs: t.Any) -> dict[str, t.Any]:
    ctx = app_ctx._get_current_object()

    if kwargs.get("max_age") is None:
        kwargs["max_age"] = ctx.app.get_send_file_max_age

    kwargs.update(
        environ=ctx.request.environ,
        use_x_sendfile=ctx.app.config["USE_X_SENDFILE"],
        response_class=ctx.app.response_class,
        _root_path=ctx.app.root_path,
    )
    return kwargs


def send_file(
    path_or_file: os.PathLike[t.AnyStr] | str | t.IO[bytes],
    mimetype: str | None = None,
    as_attachment: bool = False,
    download_name: str | None = None,
    conditional: bool = True,
    etag: bool | str = True,
    last_modified: datetime | int | float | None = None,
    max_age: None | (int | t.Callable[[str | None], int | None]) = None,
) -> Response:
    """将文件的内容发送给客户端。

    第一个参数可以是文件路径或类似文件的对象。在大多数情况下，路径是首选，因为 Werkzeug
    可以管理文件并从路径获取额外信息。传递类似文件的对象需要以二进制模式打开文件，并且在
    使用 :class:`io.BytesIO` 在内存中构建文件时最有用。

    切勿传递用户提供的文件路径。该路径被假定为受信任的，因此用户可以创建一个路径来访问您
    不希望访问的文件。使用 :func:`send_from_directory` 从目录中安全地提供用户请求的路径
    。

    如果 WSGI 服务器在 ``environ`` 中设置了 ``file_wrapper`` ，则使用它，否则使用
    Werkzeug 的内置包装器。或者，如果 HTTP 服务器支持 ``X-Sendfile`` ，则使用
    ``USE_X_SENDFILE = True`` 配置 Flask 将告诉服务器发送给定路径，这比在 Python 中读取
    它要高效得多。

    :param path_or_file: 要发送的文件的路径，相对于当前工作目录（如果给出相对路径）。或
        者，以二进制模式打开的类似文件的对象；如果已经读取，请确保调用 ``seek(0)`` 。
    :param mimetype: 要发送的文件的 MIME 类型。如果未提供，它将尝试从文件名中检测它。
    :param as_attachment: 指示浏览器应提供保存文件而不是显示文件的功能。
    :param download_name: 保存文件时浏览器将使用的默认名称。默认为传递的文件名。
    :param conditional: 根据请求标头启用条件和范围响应。需要传递文件路径和 ``environ``
        。
    :param etag: 计算文件的 ETag，这需要传递文件路径。也可以用字符串来代替。
    :param last_modified: 发送文件的最后修改时间（以秒为单位）。如果未提供，它将尝试从文
        件路径中检测它。
    :param max_age: 客户端应缓存文件多长时间（以秒为单位）。如果设置， ``Cache-Control``
        将是 ``public`` ，否则它将是 ``no-cache`` 以首选条件缓存。

    .. versionchanged:: 2.0
        ``download_name`` 替换 ``attachment_filename`` 参数。如果是
        ``as_attachment=False`` ，则改为使用 ``Content-Disposition: inline`` 传递。

    .. versionchanged:: 2.0
        ``max_age`` 替换 ``cache_timeout`` 参数。默认情况下，启用 ``conditional`` ，未设
        置 ``max_age`` 。

    .. versionchanged:: 2.0
        ``etag`` 替换 ``add_etags`` 参数。它可以是一个字符串来使用而不是生成一个。

    .. versionchanged:: 2.0
        传递继承自 :class:`~io.TextIOBase` 的类文件对象将引发 :exc:`ValueError` 而不是发
        送空文件。

    .. versionadded:: 2.0
        将实现移至 Werkzeug。现在这是一个包装器，用于传递一些 Flask 特定的参数。

    .. versionchanged:: 1.1
        ``filename`` 可能是 :class:`~os.PathLike` 对象。

    .. versionchanged:: 1.1
        传递 :class:`~io.BytesIO` 对象支持范围请求。

    .. versionchanged:: 1.0.3
        文件名使用 ASCII 而不是 Latin-1 进行编码，以便与 WSGI 服务器更广泛地兼容。

    .. versionchanged:: 1.0
        支持 :rfc:`2231` 中指定的 UTF-8 文件名。

    .. versionchanged:: 0.12
        文件名不再自动从文件对象推断。如果您想使用自动 MIME 和 etag 支持，请通过
        ``filename_or_fp`` 或 ``attachment_filename`` 传递文件名。

    .. versionchanged:: 0.12
        对于 MIME 检测， ``attachment_filename`` 优于 ``filename`` 。

    .. versionchanged:: 0.9
        ``cache_timeout`` 默认为 :meth:`Flask.get_send_file_max_age` 。

    .. versionchanged:: 0.7
        MIME 对类文件对象的猜测和 etag 支持已被删除，因为它不可靠。如果可以，请传递文件
        名，否则请自行附加 etag。

    .. versionchanged:: 0.5
        添加了 ``add_etags`` 、 ``cache_timeout`` 和 ``conditional`` 参数。默认行为是添
        加 etag。

    .. versionadded:: 0.2
    """
    return werkzeug.utils.send_file(  # type: ignore[return-value]
        **_prepare_send_file_kwargs(
            path_or_file=path_or_file,
            environ=request.environ,
            mimetype=mimetype,
            as_attachment=as_attachment,
            download_name=download_name,
            conditional=conditional,
            etag=etag,
            last_modified=last_modified,
            max_age=max_age,
        )
    )


def send_from_directory(
    directory: os.PathLike[str] | str,
    path: os.PathLike[str] | str,
    **kwargs: t.Any,
) -> Response:
    """使用 :func:`send_file` 从目录中发送文件。

    .. code-block:: python

        @app.route("/uploads/<path:name>")
        def download_file(name):
            return send_from_directory(
                app.config['UPLOAD_FOLDER'], name, as_attachment=True
            )

    这是一种从文件夹提供文件（例如静态文件或上传）的安全方法。使用
    :func:`~werkzeug.security.safe_join` 确保来自客户端的路径不会被恶意构造为指向指定目
    录之外。

    如果最终路径不指向现有的常规文件，则会引发 404 :exc:`~werkzeug.exceptions.NotFound`
    错误。

    :param directory: ``path`` 必须位于相对于当前应用程序根路径的目录。这*不能*是客户端
        提供的值，否则它会变得不安全。
    :param path: 要发送的文件的路径，相对于 ``directory`` 。
    :param kwargs: 要传递给 :func:`send_file` 的参数。

    .. versionchanged:: 2.0
        ``path`` 替换 ``filename`` 参数。

    .. versionadded:: 2.0
        将实现移至 Werkzeug。现在这是一个包装器，用于传递一些 Flask 特定的参数。

    .. versionadded:: 0.5
    """
    return werkzeug.utils.send_from_directory(  # type: ignore[return-value]
        directory, path, **_prepare_send_file_kwargs(**kwargs)
    )


def get_root_path(import_name: str) -> str:
    """查找包的根路径或包含模块的目录。如果无法找到，则返回当前工作目录。

    不要与 :func:`find_package` 返回的值混淆。

    ：元私有：
    """
    # 模块已导入并具有文件属性。先用那个。
    mod = sys.modules.get(import_name)

    if mod is not None and hasattr(mod, "__file__") and mod.__file__ is not None:
        return os.path.dirname(os.path.abspath(mod.__file__))

    # 下一步尝试：检查装载机。
    try:
        spec = importlib.util.find_spec(import_name)

        if spec is None:
            raise ValueError
    except (ImportError, ValueError):
        loader = None
    else:
        loader = spec.loader

    # 加载程序不存在，或者我们指的是已卸载的主模块或没有路径的主模块（交互式会话），请使
    # 用当前工作目录。
    if loader is None:
        return os.getcwd()

    if hasattr(loader, "get_filename"):
        filepath = loader.get_filename(import_name)  # pyright: ignore
    else:
        # 回归进口。
        __import__(import_name)
        mod = sys.modules[import_name]
        filepath = getattr(mod, "__file__", None)

        # 如果我们没有文件路径，可能是因为它是命名空间包。在这种情况下，从包中包含的第一
        # 个模块中选择根路径。
        if filepath is None:
            raise RuntimeError(
                "No root path can be found for the provided module"
                f" {import_name!r}. This can happen because the module"
                " came from an import hook that does not provide file"
                " name information or because it's a namespace package."
                " In this case the root path needs to be explicitly"
                " provided."
            )

    # 对于模块，文件路径是 import_name.py；对于包，文件路径是 __init__.py。
    return os.path.dirname(os.path.abspath(filepath))  # type: ignore[no-any-return]


@cache
def _split_blueprint_path(name: str) -> list[str]:
    out: list[str] = [name]

    if "." in name:
        out.extend(_split_blueprint_path(name.rpartition(".")[0]))

    return out


class _CollectErrors:
    """记录并消除其中引发的错误的上下文管理器。用于运行所有清理函数，然后引发任何错误。"""

    def __init__(self) -> None:
        self.errors: list[BaseException] = []

    def __enter__(self) -> None:
        pass

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_val is not None:
            self.errors.append(exc_val)

        return True

    def raise_any(self, message: str) -> None:
        """如果收集到任何错误，则提出。"""
        if self.errors:
            raise BaseExceptionGroup(message, self.errors)
