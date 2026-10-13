Subclassing Flask
=================

The :class:`~flask.Flask` class is designed for subclassing.

For example, you may want to override how request parameters are handled to preserve their order::

    from flask import Flask, Request
    from werkzeug.datastructures import ImmutableOrderedMultiDict
    class MyRequest(Request):
        """Request 子类覆盖请求参数存储"""
        parameter_storage_class = ImmutableOrderedMultiDict
    class MyFlask(Flask):
        """使用自定义请求类的 Flask 子类"""
        request_class = MyRequest

This is the recommended approach for overriding or augmenting Flask's internal functionality.
