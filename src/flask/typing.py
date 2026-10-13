from __future__ import annotations

import collections.abc as cabc
import typing as t

if t.TYPE_CHECKING:  # pragma: no cover
    from _typeshed.wsgi import WSGIApplication  # noqa: F401
    from werkzeug.datastructures import Headers  # noqa: F401
    from werkzeug.sansio.response import Response  # noqa: F401

# 可直接转换的可能类型或者是 Response 对象。
ResponseValue = t.Union[
    "Response",
    str,
    bytes,
    list[t.Any],
    # 实际只接受 dict，但使用 Mapping 注解可支持 TypedDict。
    t.Mapping[str, t.Any],
    t.Iterator[str],
    t.Iterator[bytes],
    cabc.AsyncIterable[str],  # 在 App 支持泛型之前，为 Quart 提供此类型。
    cabc.AsyncIterable[bytes],
]

# 单个 HTTP 标头的可能类型
HeaderValue = str | list[str] | tuple[str, ...]

# HTTP 标头的可能类型
HeadersValue = t.Union[
    "Headers",
    t.Mapping[str, HeaderValue],
    t.Sequence[tuple[str, HeaderValue]],
]

# 路由函数返回的可能类型。
ResponseReturnValue = t.Union[
    ResponseValue,
    tuple[ResponseValue, HeadersValue],
    tuple[ResponseValue, int],
    tuple[ResponseValue, int, HeadersValue],
    "WSGIApplication",
]

# 允许 werkzeug.Response 的任何子类（例如 Flask 中的子类）作为回调参数。直接使用
# werkzeug.Response 会使带有 flask.Response 注释的回调失败类型检查。
ResponseClass = t.TypeVar("ResponseClass", bound="Response")

AppOrBlueprintKey = str | None  # 应用使用 None 作为键，蓝图使用其名称。
AfterRequestCallable = (
    t.Callable[[ResponseClass], ResponseClass]
    | t.Callable[[ResponseClass], t.Awaitable[ResponseClass]]
)
BeforeFirstRequestCallable = t.Callable[[], None] | t.Callable[[], t.Awaitable[None]]
BeforeRequestCallable = (
    t.Callable[[], ResponseReturnValue | None]
    | t.Callable[[], t.Awaitable[ResponseReturnValue | None]]
)
ShellContextProcessorCallable = t.Callable[[], dict[str, t.Any]]
TeardownCallable = (
    t.Callable[[BaseException | None], None]
    | t.Callable[[BaseException | None], t.Awaitable[None]]
)
TemplateContextProcessorCallable = (
    t.Callable[[], dict[str, t.Any]] | t.Callable[[], t.Awaitable[dict[str, t.Any]]]
)
TemplateFilterCallable = t.Callable[..., t.Any]
TemplateGlobalCallable = t.Callable[..., t.Any]
TemplateTestCallable = t.Callable[..., bool]
URLDefaultCallable = t.Callable[[str, dict[str, t.Any]], None]
URLValuePreprocessorCallable = t.Callable[[str | None, dict[str, t.Any] | None], None]

# 此处本应接受 Exception，但这会影响特定异常类型的参数注解，
# 或影响使用不同异常多次装饰同一函数的情况（参数使用联合类型）。
# https://github.com/pallets/flask/issues/4095
# https://github.com/pallets/flask/issues/4295
# https://github.com/pallets/flask/issues/4297
ErrorHandlerCallable = (
    t.Callable[[t.Any], ResponseReturnValue]
    | t.Callable[[t.Any], t.Awaitable[ResponseReturnValue]]
)

RouteCallable = (
    t.Callable[..., ResponseReturnValue]
    | t.Callable[..., t.Awaitable[ResponseReturnValue]]
)
