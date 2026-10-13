from __future__ import annotations

import collections.abc as c
import hashlib
import typing as t
from collections.abc import MutableMapping
from datetime import datetime
from datetime import UTC

from itsdangerous import BadSignature
from itsdangerous import URLSafeTimedSerializer
from werkzeug.datastructures import CallbackDict

from .json.tag import TaggedJSONSerializer

if t.TYPE_CHECKING:  # pragma: no cover
    import typing_extensions as te

    from .app import Flask
    from .wrappers import Request
    from .wrappers import Response


class SessionMixin(MutableMapping[str, t.Any]):
    """使用会话属性扩展基本字典。"""

    @property
    def permanent(self) -> bool:
        """这反映了字典中的 ``'_permanent'`` 键。"""
        return self.get("_permanent", False)  # type: ignore[no-any-return]

    @permanent.setter
    def permanent(self, value: bool) -> None:
        self["_permanent"] = bool(value)

    #: 某些实现可以检测会话是否是新创建的，但这并不能保证。谨慎使用。混音
    # 默认为硬编码 ``False`` 。
    new = False

    #: 某些实现可以检测会话的更改并在发生更改时进行设置。 mixin 默认值被硬编码为
    #: ``True`` 。
    modified = True

    accessed = False
    """指示会话是否被访问，即使它没有被修改。当通过请求上下文（包括全局 :data:`.session`
    代理）访问会话对象时，会设置此值。如果是 ``True`` ，则会添加 ``Vary: cookie`` 标头。

    .. versionchanged:: 3.1.3
        这是由请求上下文跟踪的。
    """


class SecureCookieSession(CallbackDict[str, t.Any], SessionMixin):
    """基于签名 cookie 的会话的基类。

    该会话后端将设置 :attr:`modified` 和 :attr:`accessed` 属性。它无法可靠地跟踪会话是否
    是新的（还是空的），因此 :attr:`new` 仍然硬编码为 ``False`` 。
    """

    #: 当数据更改时，它被设置为 ``True`` 。仅跟踪会话字典本身；如果会话包含可变数据（例
    #: 如嵌套字典），则在修改该数据时必须手动将其设置为 ``True`` 。仅当这是 ``True`` 时
    #: ，会话 cookie 才会写入响应。
    modified = False

    def __init__(
        self,
        initial: c.Mapping[str, t.Any] | None = None,
    ) -> None:
        def on_update(self: te.Self) -> None:
            self.modified = True

        super().__init__(initial, on_update)


class NullSession(SecureCookieSession):
    """用于在会话不可用时生成更好的错误消息的类。  仍将允许对空会话进行只读访问，但设置
    失败。
    """

    def _fail(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        raise RuntimeError(
            "The session is unavailable because no secret "
            "key was set.  Set the secret_key on the "
            "application to something unique and secret."
        )

    __setitem__ = __delitem__ = clear = pop = popitem = update = setdefault = _fail
    del _fail


class SessionInterface:
    """为了替换使用 werkzeug 的 securecookie 实现的默认会话接口，您必须实现基本接口。
    您必须实现的唯一方法是 :meth:`open_session` 和 :meth:`save_session` ，其他方法具有有
    用的默认值，您无需更改。

    :meth:`open_session` 方法返回的会话对象必须提供类似字典的接口以及
    :class:`SessionMixin` 中的属性和方法。  我们建议只对 dict 进行子类化并添加 mixin::

        class Session(dict, SessionMixin):
            pass

    如果 :meth:`open_session` 返回 ``None`` Flask 将调用 :meth:`make_null_session` 来创
    建一个会话，如果会话支持因某些要求未满足而无法工作，则该会话将充当替换。  创建的默认
    :class:`NullSession` 类会抱怨未设置密钥。

    要替换应用程序上的会话接口，您只需分配 :attr:`flask.Flask.session_interface` ::

        app = Flask(__name__)
        app.session_interface = MySessionInterface()

    可以同时发送和处理同一会话的多个请求。实现新的会话接口时，请考虑是否必须同步对后备存
    储的读取或写入。无法保证每个请求的会话打开或保存的顺序，它将按照请求开始和结束处理的
    顺序发生。

    .. versionadded:: 0.8
    """

    #: :meth:`make_null_session` 将在此处查找请求空会话时应创建的类。  同样，
    #: :meth:`is_null_session` 方法将针对此类型执行类型检查。
    null_session_class = NullSession

    #: 指示会话接口是否基于 pickle 的标志。 Flask 扩展可以使用它来决定如何处理会话对象。
    #:
    #: .. versionadded:: 0.10
    pickle_based = False

    def make_null_session(self, app: Flask) -> NullSession:
        """创建一个空会话，如果由于配置错误而无法加载真实会话支持，则该会话充当替换对象
        。  这主要有助于改善用户体验，因为空会话的工作仍然支持查找而不抱怨，但修改会通过
        失败的有用错误消息来回答。

        默认情况下，这会创建 :attr:`null_session_class` 的实例。
        """
        return self.null_session_class()

    def is_null_session(self, obj: object) -> bool:
        """检查给定对象是否为空会话。  不要求保存空会话。

        默认情况下，这会检查该对象是否是 :attr:`null_session_class` 的实例。
        """
        return isinstance(obj, self.null_session_class)

    def get_cookie_name(self, app: Flask) -> str:
        """会话 cookie 的名称。使用 ``app.config["SESSION_COOKIE_NAME"]`` 。"""
        return app.config["SESSION_COOKIE_NAME"]  # type: ignore[no-any-return]

    def get_cookie_domain(self, app: Flask) -> str | None:
        """会话 cookie 上 ``Domain`` 参数的值。如果未设置，浏览器只会将 cookie 发送到设
        置它的确切域。否则，他们也会将其发送到给定值的任何子域。

        使用 :data:`SESSION_COOKIE_DOMAIN` 配置。

        .. versionchanged:: 2.3
            默认情况下未设置，不会回退到 ``SERVER_NAME`` 。
        """
        return app.config["SESSION_COOKIE_DOMAIN"]  # type: ignore[no-any-return]

    def get_cookie_path(self, app: Flask) -> str:
        """返回 cookie 应该有效的路径。  默认实现使用 ``SESSION_COOKIE_PATH`` 配置变量中
        的值（如果已设置），并回退到 ``APPLICATION_ROOT`` 或使用 ``/`` （如果是 ``None``
        ）。
        """
        return app.config["SESSION_COOKIE_PATH"] or app.config["APPLICATION_ROOT"]  # type: ignore[no-any-return]

    def get_cookie_httponly(self, app: Flask) -> bool:
        """如果会话 cookie 应该是 httponly，则返回 True。  目前，这仅返回
        ``SESSION_COOKIE_HTTPONLY`` 配置变量的值。
        """
        return app.config["SESSION_COOKIE_HTTPONLY"]  # type: ignore[no-any-return]

    def get_cookie_secure(self, app: Flask) -> bool:
        """如果 cookie 应该是安全的，则返回 True。  目前，这仅返回
        ``SESSION_COOKIE_SECURE`` 设置的值。
        """
        return app.config["SESSION_COOKIE_SECURE"]  # type: ignore[no-any-return]

    def get_cookie_samesite(self, app: Flask) -> str | None:
        """如果 cookie 应使用 ``SameSite`` 属性，则返回 ``'Strict'`` 或 ``'Lax'`` 。目前
        ，这仅返回 :data:`SESSION_COOKIE_SAMESITE` 设置的值。
        """
        return app.config["SESSION_COOKIE_SAMESITE"]  # type: ignore[no-any-return]

    def get_cookie_partitioned(self, app: Flask) -> bool:
        """如果应该对 cookie 进行分区，则返回 True。默认情况下，使用
        :data:`SESSION_COOKIE_PARTITIONED` 的值。

        .. versionadded:: 3.1
        """
        return app.config["SESSION_COOKIE_PARTITIONED"]  # type: ignore[no-any-return]

    def get_expiration_time(self, app: Flask, session: SessionMixin) -> datetime | None:
        """一个帮助器方法，返回会话的到期日期或 ``None`` （如果会话链接到浏览器会话）。
        默认实现返回 now + 应用程序上配置的永久会话生存期。
        """
        if session.permanent:
            return datetime.now(UTC) + app.permanent_session_lifetime
        return None

    def should_set_cookie(self, app: Flask, session: SessionMixin) -> bool:
        """会话后端使用它来确定是否应为此响应的会话 cookie 设置 ``Set-Cookie`` 标头。如
        果会话已被修改，则设置 cookie。如果会话是永久的并且
        ``SESSION_REFRESH_EACH_REQUEST`` 配置为 true，则始终设置 cookie。

        如果会话被删除，则通常会跳过此检查。

        .. versionadded:: 0.11
        """

        return session.modified or (
            session.permanent and app.config["SESSION_REFRESH_EACH_REQUEST"]
        )

    def open_session(self, app: Flask, request: Request) -> SessionMixin | None:
        """这在每个请求开始时、推送请求上下文之后、匹配 URL 之前调用。

        这必须返回一个实现类字典接口以及 :class:`SessionMixin` 接口的对象。

        这将返回 ``None`` 来指示加载以某种方式失败，但不会立即出现错误。在这种情况下，请
        求上下文将回退到使用 :meth:`make_null_session` 。
        """
        raise NotImplementedError()

    def save_session(
        self, app: Flask, session: SessionMixin, response: Response
    ) -> None:
        """这是在每个请求结束时、生成响应之后、删除请求上下文之前调用的。如果
        :meth:`is_null_session` 返回 ``True`` ，则跳过它。
        """
        raise NotImplementedError()


session_json_serializer = TaggedJSONSerializer()


def _lazy_sha1(string: bytes = b"") -> t.Any:
    """在运行时之前不要访问 ``hashlib.sha1`` 。 FIPS 版本可能不包含 SHA-1，在这种情况下
    ，在开发人员可以配置其他内容之前，作为默认值导入和使用将会失败。
    """
    return hashlib.sha1(string)


class SecureCookieSessionInterface(SessionInterface):
    """默认会话接口，通过 :mod:`itsdangerous` 模块将会话存储在签名的 cookie 中。"""

    #: 应用于签署基于 cookie 的会话的密钥之上的盐。
    salt = "cookie-session"
    #: 用于签名的哈希函数。  默认是sha1
    digest_method = staticmethod(_lazy_sha1)
    #: itsdangerous 支持的密钥派生的名称。  默认为 hmac。
    key_derivation = "hmac"
    #: 有效负载的 python 序列化器。  默认是一个紧凑的 JSON 派生序列化器，支持一些额外的
    #: Python 类型，例如日期时间对象或元组。
    serializer = session_json_serializer
    session_class = SecureCookieSession

    def get_signing_serializer(self, app: Flask) -> URLSafeTimedSerializer | None:
        if not app.secret_key:
            return None

        keys: list[str | bytes] = []

        if fallbacks := app.config["SECRET_KEY_FALLBACKS"]:
            keys.extend(fallbacks)

        keys.append(app.secret_key)  # itsdangerous 预计当前键位于顶部
        return URLSafeTimedSerializer(
            keys,  # type: ignore[arg-type]
            salt=self.salt,
            serializer=self.serializer,
            signer_kwargs={
                "key_derivation": self.key_derivation,
                "digest_method": self.digest_method,
            },
        )

    def open_session(self, app: Flask, request: Request) -> SecureCookieSession | None:
        s = self.get_signing_serializer(app)
        if s is None:
            return None
        val = request.cookies.get(self.get_cookie_name(app))
        if not val:
            return self.session_class()
        max_age = int(app.permanent_session_lifetime.total_seconds())
        try:
            data = s.loads(val, max_age=max_age)
            return self.session_class(data)
        except BadSignature:
            return self.session_class()

    def save_session(
        self, app: Flask, session: SessionMixin, response: Response
    ) -> None:
        name = self.get_cookie_name(app)
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        secure = self.get_cookie_secure(app)
        partitioned = self.get_cookie_partitioned(app)
        samesite = self.get_cookie_samesite(app)
        httponly = self.get_cookie_httponly(app)

        # 如果会话被访问，请添加 "Vary: Cookie" 标头。
        if session.accessed:
            response.vary.add("Cookie")

        # 如果session修改为空，则删除cookie。如果会话为空，则返回而不设置cookie。
        if not session:
            if session.modified:
                response.delete_cookie(
                    name,
                    domain=domain,
                    path=path,
                    secure=secure,
                    partitioned=partitioned,
                    samesite=samesite,
                    httponly=httponly,
                )
                response.vary.add("Cookie")

            return

        if not self.should_set_cookie(app, session):
            return

        expires = self.get_expiration_time(app, session)
        val = self.get_signing_serializer(app).dumps(dict(session))  # type: ignore[union-attr]
        response.set_cookie(
            name,
            val,
            expires=expires,
            httponly=httponly,
            domain=domain,
            path=path,
            secure=secure,
            partitioned=partitioned,
            samesite=samesite,
        )
        response.vary.add("Cookie")
