import flask
from flask.globals import app_ctx
from flask.sessions import SessionInterface


def test_open_session_with_endpoint():
    """如果加载会话时需要 request.endpoint （或其他 URL 匹配行为），则可以手动调用
    RequestContext.match_request() 。
    """

    class MySessionInterface(SessionInterface):
        def save_session(self, app, session, response):
            pass

        def open_session(self, app, request):
            app_ctx.match_request()
            assert request.endpoint is not None

    app = flask.Flask(__name__)
    app.session_interface = MySessionInterface()

    @app.get("/")
    def index():
        return "Hello, World!"

    response = app.test_client().get("/")
    assert response.status_code == 200
