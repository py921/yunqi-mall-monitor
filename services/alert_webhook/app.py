"""alert-webhook —— 接收 Alertmanager 告警并落地。

默认行为：把告警按等级打印到容器日志，并在内存里保留最近 200 条，
通过 GET /alerts 可以直接查看，方便在没有钉钉/企微环境时验证告警链路。

对接钉钉只需实现 _send_to_dingtalk()，见 README「接入钉钉」章节。
"""
import logging
import os
import threading
import time
from collections import deque

from flask import jsonify, request
from prometheus_client import Counter, Gauge

from common.metrics import metrics_payload
from common.web import create_app

SERVICE = "alert-webhook"
VERSION = os.getenv("APP_VERSION", "1.0.0")

MAX_KEEP = int(os.getenv("ALERT_KEEP", "200"))
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "").strip()

ALERTS_RECEIVED = Counter(
    "alert_webhook_received_total", "接收到的告警条数", ["service", "severity", "status"]
)
ALERTS_FIRING = Gauge(
    "alert_webhook_firing_count", "当前处于触发状态的告警数", ["service", "severity"]
)

app = create_app(SERVICE, VERSION)
logger: logging.Logger = app.extensions["logger"]

_lock = threading.Lock()
_recent: deque = deque(maxlen=MAX_KEEP)
_firing: dict = {}


def _severity_of(labels: dict) -> str:
    return str(labels.get("severity") or "P2")


def _send_to_dingtalk(severity: str, title: str, text: str) -> None:
    """钉钉机器人推送（默认关闭，配置 DINGTALK_WEBHOOK 后自动启用）。"""
    if not DINGTALK_WEBHOOK:
        return
    try:
        import requests
        requests.post(
            DINGTALK_WEBHOOK,
            json={"msgtype": "markdown", "markdown": {
                "title": f"[{severity}] {title}",
                "text": f"### [{severity}] {title}\n\n{text}",
            }},
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("钉钉推送失败", extra={"extra_fields": {"reason": str(exc)}})


def _handle(payload: dict, severity_hint: str | None = None) -> None:
    for item in payload.get("alerts", []):
        labels = item.get("labels", {})
        annotations = item.get("annotations", {})
        severity = _severity_of(labels) if not severity_hint else severity_hint
        status = item.get("status", "firing")
        service = labels.get("service", "unknown")
        summary = annotations.get("summary", "(无摘要)")
        description = annotations.get("description", "")

        ALERTS_RECEIVED.labels(SERVICE, severity, status).inc()

        with _lock:
            key = f"{labels.get('alertname')}|{service}"
            if status == "firing":
                _firing[key] = {
                    "severity": severity, "service": service,
                    "summary": summary, "since": item.get("startsAt"),
                }
            else:
                _firing.pop(key, None)
            ALERTS_FIRING.labels(SERVICE, severity).set(
                sum(1 for v in _firing.values() if v["severity"] == severity))
            _recent.append({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": status, "severity": severity, "service": service,
                "alertname": labels.get("alertname"), "summary": summary,
                "description": description,
            })

        if status == "firing":
            logger.error("告警触发 [%s] %s | %s", severity, summary, description,
                         extra={"extra_fields": {
                             "severity": severity, "service": service,
                             "alertname": labels.get("alertname")}})
            _send_to_dingtalk(severity, summary, f"{description}\n\n服务：{service}")
        else:
            logger.info("告警恢复 [%s] %s", severity, summary,
                        extra={"extra_fields": {"severity": severity,
                                                "service": service}})


@app.route("/webhook", methods=["POST"])
@app.route("/webhook/<severity>", methods=["POST"])
def webhook(severity: str | None = None):
    payload = request.get_json(silent=True) or {}
    _handle(payload, severity.upper() if severity else None)
    return jsonify(code=0, message="received")


@app.route("/alerts", methods=["GET"])
def list_alerts():
    with _lock:
        return jsonify(
            code=0,
            firing_count=len(_firing),
            firing=list(_firing.values()),
            recent=list(_recent)[-30:][::-1],
        )


@app.route("/metrics", methods=["GET"])
def metrics():
    return metrics_payload()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
