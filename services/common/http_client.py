"""带打点的服务间调用客户端。

所有跨服务调用都必须走这里，这样 downstream_* 指标才是完整的，
Grafana 上才能画出「谁调谁、成功多少、慢在哪」。
"""
import os
import time

import requests

from .metrics import DOWNSTREAM_LATENCY, DOWNSTREAM_REQUESTS

SERVICE_URLS = {
    "order-service": os.getenv("ORDER_SERVICE_URL", "http://order-service:8001"),
    "pay-service": os.getenv("PAY_SERVICE_URL", "http://pay-service:8002"),
    "user-service": os.getenv("USER_SERVICE_URL", "http://user-service:8003"),
}

DEFAULT_TIMEOUT = float(os.getenv("DOWNSTREAM_TIMEOUT", "2.5"))


class DownstreamError(RuntimeError):
    """下游服务不可用或返回非 2xx。"""

    def __init__(self, target: str, status: str, detail: str = ""):
        super().__init__(f"{target} 调用失败（{status}）{detail}")
        self.target = target
        self.status = status


def call(caller: str, target: str, method: str, path: str,
         json_body=None, timeout: float | None = None) -> dict:
    """调用下游服务并自动记录调用量 / 耗时指标。"""
    url = SERVICE_URLS[target].rstrip("/") + path
    started = time.perf_counter()
    status = "error"
    try:
        response = requests.request(
            method, url, json=json_body, timeout=timeout or DEFAULT_TIMEOUT
        )
        status = str(response.status_code)
        if response.status_code >= 500:
            raise DownstreamError(target, status, response.text[:120])
        return response.json() if response.content else {}
    except requests.Timeout as exc:
        status = "timeout"
        raise DownstreamError(target, status) from exc
    except requests.RequestException as exc:
        status = "unreachable"
        raise DownstreamError(target, status, str(exc)[:120]) from exc
    finally:
        DOWNSTREAM_REQUESTS.labels(caller, target, status).inc()
        DOWNSTREAM_LATENCY.labels(caller, target).observe(
            time.perf_counter() - started
        )
