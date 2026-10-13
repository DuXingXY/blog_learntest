from flaskr import create_app


def test_config():
    """测试 create_app 而不通过测试配置。"""
    assert not create_app().testing
    assert create_app({"TESTING": True}).testing


def test_hello(client):
    response = client.get("/hello")
    assert response.data == b"Hello, World!"
