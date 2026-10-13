import os

from flask import Flask


def create_app(test_config=None):
    """创建并配置 Flask 应用程序的实例。"""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        # 默认密钥应由实例配置覆盖
        SECRET_KEY="dev",
        # 将数据库存储在实例文件夹中
        DATABASE=os.path.join(app.instance_path, "flaskr.sqlite"),
    )

    if test_config is None:
        # 不测试时加载实例配置（如果存在）
        app.config.from_pyfile("config.py", silent=True)
    else:
        # 如果传入则加载测试配置
        app.config.update(test_config)

    # 确保实例文件夹存在
    os.makedirs(app.instance_path, exist_ok=True)

    @app.route("/hello")
    def hello():
        return "Hello, World!"

    # 注册数据库命令
    from . import db

    db.init_app(app)

    # 将蓝图应用到应用程序
    from . import auth
    from . import blog

    app.register_blueprint(auth.bp)
    app.register_blueprint(blog.bp)

    # 使 url_for('index') == url_for('blog.index')。
    # 其他应用可在此用 app.route 定义独立首页，并为博客蓝图设置 url_prefix。
    # 本教程直接将博客页面作为首页。
    app.add_url_rule("/", endpoint="index")

    return app
