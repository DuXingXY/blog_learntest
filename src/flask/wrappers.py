from __future__ import annotations

import typing as t

from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Request as RequestBase
from werkzeug.wrappers import Response as ResponseBase

from . import json
from .globals import current_app
from .helpers import _split_blueprint_path

if t.TYPE_CHECKING:  # pragma: no cover
    from werkzeug.routing import Rule


class Request(RequestBase):
    """Flask 中默认使用的请求对象。  记住匹配的端点和视图参数。

    这就是最终的 :class:`~flask.request` 。  如果您想替换所使用的请求对象，您可以对其进
    行子类化并将 :attr:`~flask.Flask.request_class` 设置为您的子类。

    请求对象是 :class:`~werkzeug.wrappers.Request` 子类，提供 Werkzeug 定义的所有属性以
    及一些 Flask 特定属性。
    """

    json_module: t.Any = json

    #: 与请求匹配的内部 URL 规则。  这对于检查前/后处理程序 (
    #: ``request.url_rule.methods`` ) 等中允许 URL 使用哪些方法很有用。尽管如果请求的方
    #: 法对于 URL 规则无效，则有效列表可在 ``routing_exception.valid_methods`` 中使用（
    #: Werkzeug 异常 :exc:`~werkzeug.exceptions.MethodNotAllowed` 的属性），因为请求从未
    #: 在内部绑定。
    #:
    #: .. versionadded:: 0.6
    url_rule: Rule | None = None

    #: 与请求匹配的视图参数的字典。  如果匹配时发生异常，则为 ``None`` 。
    view_args: dict[str, t.Any] | None = None

    #: 如果匹配 URL 失败，则这是将在请求处理过程中引发的异常。  这通常是
    #: :exc:`~werkzeug.exceptions.NotFound` 异常或类似的异常。
    routing_exception: HTTPException | None = None

    _max_content_length: int | None = None
    _max_form_memory_size: int | None = None
    _max_form_parts: int | None = None

    @property
    def max_content_length(self) -> int | None:
        """在此请求期间将读取的最大字节数。如果超出此限制，则会引发 413
        :exc:`~werkzeug.exceptions.RequestEntityTooLarge` 错误。如果设置为 ``None`` ，则
        在 Flask 应用程序级别不强制实施限制。但是，如果它是 ``None`` 并且请求没有
        ``Content-Length`` 标头，并且 WSGI 服务器没有指示它终止流，则不会读取任何数据以
        避免无限流。

        每个请求默认为 :data:`MAX_CONTENT_LENGTH` 配置，默认为 ``None`` 。可以在特定的
        ``request`` 上设置它以将限制应用于该特定视图。应根据应用程序或视图的特定需求适当
        设置。

        .. versionchanged:: 3.1
            这可以根据请求进行设置。

        .. versionchanged:: 0.6
            这可以通过 Flask 配置进行配置。
        """
        if self._max_content_length is not None:
            return self._max_content_length

        if not current_app:
            return super().max_content_length

        return current_app.config["MAX_CONTENT_LENGTH"]  # type: ignore[no-any-return]

    @max_content_length.setter
    def max_content_length(self, value: int | None) -> None:
        self._max_content_length = value

    @property
    def max_form_memory_size(self) -> int | None:
        """任何非文件表单字段可以位于 ``multipart/form-data`` 主体中的最大大小（以字节为
        单位）。如果超出此限制，则会引发 413
        :exc:`~werkzeug.exceptions.RequestEntityTooLarge` 错误。如果设置为 ``None`` ，则
        在 Flask 应用程序级别不强制实施限制。

        每个请求默认为 :data:`MAX_FORM_MEMORY_SIZE` 配置，默认为 ``500_000`` 。可以在特
        定的 ``request`` 上设置它以将限制应用于该特定视图。应根据应用程序或视图的特定需
        求适当设置。

        .. versionchanged:: 3.1
            这可以通过 Flask 配置进行配置。
        """
        if self._max_form_memory_size is not None:
            return self._max_form_memory_size

        if not current_app:
            return super().max_form_memory_size

        return current_app.config["MAX_FORM_MEMORY_SIZE"]  # type: ignore[no-any-return]

    @max_form_memory_size.setter
    def max_form_memory_size(self, value: int | None) -> None:
        self._max_form_memory_size = value

    @property  # type: ignore[override]
    def max_form_parts(self) -> int | None:
        """``multipart/form-data`` 主体中可以存在的最大字段数。如果超出此限制，则会引发
        413 :exc:`~werkzeug.exceptions.RequestEntityTooLarge` 错误。如果设置为 ``None``
        ，则在 Flask 应用程序级别不强制实施限制。

        每个请求默认为 :data:`MAX_FORM_PARTS` 配置，默认为 ``1_000`` 。可以在特定的
        ``request`` 上设置它以将限制应用于该特定视图。应根据应用程序或视图的特定需求适当
        设置。

        .. versionchanged:: 3.1
            这可以通过 Flask 配置进行配置。
        """
        if self._max_form_parts is not None:
            return self._max_form_parts

        if not current_app:
            return super().max_form_parts

        return current_app.config["MAX_FORM_PARTS"]  # type: ignore[no-any-return]

    @max_form_parts.setter
    def max_form_parts(self, value: int | None) -> None:
        self._max_form_parts = value

    @property
    def endpoint(self) -> str | None:
        """与请求 URL 匹配的端点。

        如果匹配失败或尚未执行，这将是 ``None`` 。

        这与 :attr:`view_args` 结合可用于重建相同的 URL 或修改后的 URL。
        """
        if self.url_rule is not None:
            return self.url_rule.endpoint  # type: ignore[no-any-return]

        return None

    @property
    def blueprint(self) -> str | None:
        """当前蓝图的注册名称。

        如果端点不是蓝图的一部分，或者 URL 匹配失败或尚未执行，则这将为 ``None`` 。

        这不一定与创建蓝图时使用的名称匹配。它可能已嵌套，或使用不同的名称注册。
        """
        endpoint = self.endpoint

        if endpoint is not None and "." in endpoint:
            return endpoint.rpartition(".")[0]

        return None

    @property
    def blueprints(self) -> list[str]:
        """当前蓝图向上到父蓝图的注册名称。

        如果没有当前蓝图，或者 URL 匹配失败，这将是一个空列表。

        .. versionadded:: 2.0.1
        """
        name = self.blueprint

        if name is None:
            return []

        return _split_blueprint_path(name)

    def _load_form_data(self) -> None:
        super()._load_form_data()

        # 在调试模式下，我们将文件 multidict 替换为一个临时子类，该子类会针对关键错误引
        # 发不同的错误。
        if (
            current_app
            and current_app.debug
            and self.mimetype != "multipart/form-data"
            and not self.files
        ):
            from .debughelpers import attach_enctype_error_multidict

            attach_enctype_error_multidict(self)

    def on_json_loading_failed(self, e: ValueError | None) -> t.Any:
        try:
            return super().on_json_loading_failed(e)
        except BadRequest as ebr:
            if current_app and current_app.debug:
                raise

            raise BadRequest() from ebr


class Response(ResponseBase):
    """Flask 中默认使用的响应对象。  与 Werkzeug 的响应对象类似，但默认设置为具有 HTML
    mimetype。  很多时候你不必自己创建这个对象，因为 :meth:`~flask.Flask.make_response`
    会为你处理这个事情。

    如果您想替换所使用的响应对象，您可以对其进行子类化并将
    :attr:`~flask.Flask.response_class` 设置为您的子类。

    .. versionchanged:: 1.0
        与请求一样，将 JSON 支持添加到响应中。当测试以 JSON 形式获取测试客户端响应数据时
        ，这非常有用。

    .. versionchanged:: 1.0

        添加了 :attr:`max_cookie_size` 。
    """

    default_mimetype: str | None = "text/html"

    json_module = json

    autocorrect_location_header = False

    @property
    def max_cookie_size(self) -> int:  # type: ignore
        """:data:`MAX_COOKIE_SIZE` 配置键的只读视图。

        请参阅 Werkzeug 文档中的 :attr:`~werkzeug.wrappers.Response.max_cookie_size` 。
        """
        if current_app:
            return current_app.config["MAX_COOKIE_SIZE"]  # type: ignore[no-any-return]

        # 当不在应用程序上下文中时返回 Werkzeug 的默认值
        return super().max_cookie_size
