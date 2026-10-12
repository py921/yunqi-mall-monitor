"""统一指标定义。

三个微服务复用同一套指标命名规范，靠 label 区分服务实例，
因此在 Grafana / PromQL 里一条查询就能覆盖全部服务，
不需要为每个服务单独写一份表达式。
"""
import time
from functools import wraps

from flask import Response, request
from prometheus_client import (CONTENT_TYPE_LATEST, Counter, Gauge, Histogram,
                               generate_latest)

# --------------------------------------------------------------------------- #
# HTTP 层指标（每个服务都会产生）
# --------------------------------------------------------------------------- #
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["service", "method", "endpoint", "status"],
)

HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["service", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

HTTP_INFLIGHT = Gauge(
    "http_requests_inflight",
    "当前正在处理的请求数",
    ["service"],
)

# --------------------------------------------------------------------------- #
# 服务间调用指标 —— 微服务监控区别于单体监控的核心
# --------------------------------------------------------------------------- #
DOWNSTREAM_REQUESTS = Counter(
    "downstream_requests_total",
    "服务间调用总数",
    ["caller", "target", "status"],
)

DOWNSTREAM_LATENCY = Histogram(
    "downstream_request_duration_seconds",
    "服务间调用耗时（秒）",
    ["caller", "target"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

# --------------------------------------------------------------------------- #
# 服务元信息：值恒为 1，靠 label 携带版本号/环境，便于做变更追踪
# --------------------------------------------------------------------------- #
SERVICE_INFO = Gauge(
    "service_info",
    "服务元信息（值恒为 1）",
    ["service", "version", "env"],
)


def register_service_info(service: str, version: str, env: str) -> None:
    SERVICE_INFO.labels(service, version, env).set(1)


def metrics_payload() -> Response:
    """返回 Prometheus 文本格式的指标。"""
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def instrument(service: str):
    """视图装饰器：自动记录请求量 / 耗时 / 并发数。

    注意 endpoint 取的是路由模板（如 /api/order/<order_no>）而不是真实路径，
    否则订单号会把标签基数打爆。
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            endpoint = request.url_rule.rule if request.url_rule else request.path
            HTTP_INFLIGHT.labels(service).inc()
            started = time.perf_counter()
            status = 500
            try:
                response = fn(*args, **kwargs)
                status = getattr(response, "status_code", 200)
                return response
            finally:
                HTTP_INFLIGHT.labels(service).dec()
                HTTP_LATENCY.labels(service, endpoint).observe(
                    time.perf_counter() - started
                )
                HTTP_REQUESTS.labels(
                    service, request.method, endpoint, str(status)
                ).inc()

        return wrapper

    return decorator
