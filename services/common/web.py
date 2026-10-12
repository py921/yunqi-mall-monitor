"""Flask 应用公共装配：健康检查、指标端点、统一错误响应。"""
import os
import time

from flask import Flask, jsonify

from .logging_conf import setup_logging
from .metrics import metrics_payload, register_service_info

START_TIME = time.time()


def create_app(service: str, version: str | None = None,
               env: str | None = None) -> Flask:
    version = version or os.getenv("APP_VERSION", "1.0.0")
    env = env or os.getenv("APP_ENV", "dev")

    app = Flask(service)
    app.json.ensure_ascii = False
    logger = setup_logging(service)
    register_service_info(service, version, env)

    app.extensions["service_name"] = service
    app.extensions["service_version"] = version
    app.extensions["logger"] = logger

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(
            status="UP",
            service=service,
            version=version,
            env=env,
            uptime_seconds=round(time.time() - START_TIME, 3),
        )

    @app.route("/metrics", methods=["GET"])
    def metrics():
        return metrics_payload()

    @app.errorhandler(404)
    def _not_found(_):
        return jsonify(code=404, message="接口不存在"), 404

    @app.errorhandler(405)
    def _method_not_allowed(_):
        return jsonify(code=405, message="请求方法不被允许"), 405

    @app.errorhandler(500)
    def _server_error(_):
        return jsonify(code=500, message="服务内部错误"), 500

    return app
