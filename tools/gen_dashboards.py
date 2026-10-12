#!/usr/bin/env python3
"""生成 Grafana 大屏 JSON（Grafana 11 规范）。

为什么用脚本而不是手写 JSON：
Grafana 的 dashboard schema 字段极多，手写极易漏字段或写出已被废弃的枚举值
（例如把 fieldConfig.defaults.color.mode 写成旧版的 "none"，会让整个大屏白屏）。
这里所有面板共用同一套字段模板，既能保证一致性，也方便以后批量加面板。

用法：
    python tools/gen_dashboards.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "grafana", "dashboards"))

# 必须与 grafana/provisioning/datasources/prometheus.yml 中的 uid 一致
DS = {"type": "prometheus", "uid": "yunqi-prometheus"}

# color.mode 的合法取值（写成别的值会导致大屏渲染崩溃）
THRESHOLDS_GREEN = {"mode": "absolute",
                    "steps": [{"color": "green", "value": None}]}


def _custom(fill=8, stacking="none", draw_style="line",
            line_width=2, show_points="never"):
    return {
        "axisBorderShow": False,
        "axisCenteredZero": False,
        "axisColorMode": "text",
        "axisLabel": "",
        "axisPlacement": "auto",
        "barAlignment": 0,
        "barWidthFactor": 0.6,
        "drawStyle": draw_style,
        "fillOpacity": fill,
        "gradientMode": "none",
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
        "insertNulls": False,
        "lineInterpolation": "smooth",
        "lineWidth": line_width,
        "pointSize": 5,
        "scaleDistribution": {"type": "linear"},
        "showPoints": show_points,
        "spanNulls": False,
        "stacking": {"group": "A", "mode": stacking},
        "thresholdsStyle": {"mode": "off"},
    }


def timeseries(pid, title, exprs, grid, unit="short",
               legend="{{service}}", fill=8, stacking="none", decimals=None):
    """时序图面板。exprs 与 legend 支持等长列表。"""
    legends = legend if isinstance(legend, list) else [legend] * len(exprs)
    defaults = {
        "color": {"mode": "palette-classic"},
        "custom": _custom(fill=fill, stacking=stacking),
        "mappings": [],
        "thresholds": THRESHOLDS_GREEN,
        "unit": unit,
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "datasource": DS,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "gridPos": grid,
        "id": pid,
        "options": {
            "legend": {"calcs": ["lastNotNull", "max"],
                       "displayMode": "table",
                       "placement": "bottom",
                       "showLegend": True},
            "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
        },
        "targets": [
            {"datasource": DS, "editorMode": "code", "expr": expr,
             "legendFormat": legends[idx], "range": True,
             "refId": chr(ord("A") + idx)}
            for idx, expr in enumerate(exprs)
        ],
        "title": title,
        "type": "timeseries",
    }


def stat(pid, title, expr, grid, unit="short", thresholds=None,
         mappings=None, decimals=None, graph="none", color_mode="thresholds"):
    """单值面板。thresholds 必须是升序的 steps 列表。"""
    defaults = {
        "color": {"mode": color_mode},
        "decimals": 2 if decimals is None else decimals,
        "mappings": mappings or [],
        "thresholds": {"mode": "absolute",
                       "steps": thresholds or THRESHOLDS_GREEN["steps"]},
        "unit": unit,
    }
    return {
        "datasource": DS,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "gridPos": grid,
        "id": pid,
        "options": {
            "colorMode": "value",
            "graphMode": graph,
            "justifyMode": "auto",
            "orientation": "auto",
            "percentChangeColorMode": "standard",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "",
                              "values": False},
            "showPercentChange": False,
            "textMode": "auto",
            "wideLayout": True,
        },
        "targets": [{"datasource": DS, "editorMode": "code", "expr": expr,
                     "instant": True, "range": False, "refId": "A"}],
        "title": title,
        "type": "stat",
    }


def table(pid, title, expr, grid, unit="short"):
    """表格面板，用于服务健康明细这类多维度清单。"""
    return {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "custom": {
                    "align": "auto",
                    "cellOptions": {"type": "auto"},
                    "filterable": True,
                    "inspect": False,
                },
                "mappings": [],
                "thresholds": THRESHOLDS_GREEN,
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": grid,
        "id": pid,
        "options": {
            "cellHeight": "sm",
            "footer": {"countRows": False, "fields": "", "reducer": ["sum"],
                       "show": False},
            "showHeader": True,
        },
        "targets": [{"datasource": DS, "editorMode": "code", "expr": expr,
                     "format": "table", "instant": True, "range": False,
                     "refId": "A"}],
        "title": title,
        "type": "table",
    }


def dashboard(uid, title, description, tags, panels):
    return {
        "annotations": {"list": [{
            "builtIn": 1,
            "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": True, "hide": True,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts", "type": "dashboard",
        }]},
        "description": description,
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "panels": panels,
        "refresh": "10s",
        "schemaVersion": 39,
        "tags": tags,
        "templating": {"list": []},
        "time": {"from": "now-1h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
        "weekStart": "",
    }


# =========================================================================== #
# 大屏 1：微服务总览
# =========================================================================== #
UP_DOWN_MAPPING = [{
    "options": {
        "0": {"color": "red", "index": 0, "text": "DOWN"},
        "1": {"color": "green", "index": 1, "text": "UP"},
    },
    "type": "value",
}]

service_overview = dashboard(
    uid="yunqi-service-overview",
    title="云启商城 · 微服务总览",
    description="微服务可用性、流量、错误率、延迟与服务间调用质量",
    tags=["yunqi-mall", "microservice", "sre"],
    panels=[
        stat(1, "在线微服务数",
             'count(up{layer="microservice"} == 1)',
             {"h": 5, "w": 6, "x": 0, "y": 0}, unit="short", decimals=0,
             thresholds=[{"color": "red", "value": None},
                         {"color": "green", "value": 3}]),
        stat(2, "入口 QPS（全服务）",
             'sum(rate(http_requests_total{layer="microservice"}[1m]))',
             {"h": 5, "w": 6, "x": 6, "y": 0}, unit="reqps", graph="area"),
        stat(3, "整体 5xx 错误率",
             'sum(rate(http_requests_total{layer="microservice",status=~"5.."}[5m]))'
             ' / sum(rate(http_requests_total{layer="microservice"}[5m]))',
             {"h": 5, "w": 6, "x": 12, "y": 0}, unit="percentunit",
             thresholds=[{"color": "green", "value": None},
                         {"color": "yellow", "value": 0.01},
                         {"color": "red", "value": 0.05}]),
        stat(4, "整体 P99 延迟",
             'histogram_quantile(0.99, sum by (le) '
             '(rate(http_request_duration_seconds_bucket{layer="microservice"}[5m])))',
             {"h": 5, "w": 6, "x": 18, "y": 0}, unit="s",
             thresholds=[{"color": "green", "value": None},
                         {"color": "yellow", "value": 0.5},
                         {"color": "red", "value": 1}]),

        timeseries(5, "各服务 QPS",
                   ['sum by (service) (rate(http_requests_total{layer="microservice"}[1m]))'],
                   {"h": 8, "w": 12, "x": 0, "y": 5}, unit="reqps"),
        timeseries(6, "各服务错误率",
                   ['sum by (service) (rate(http_requests_total{layer="microservice",status=~"5.."}[5m]))'
                    ' / sum by (service) (rate(http_requests_total{layer="microservice"}[5m]))'],
                   {"h": 8, "w": 12, "x": 12, "y": 5}, unit="percentunit"),
        timeseries(7, "各服务 P99 延迟",
                   ['histogram_quantile(0.99, sum by (le, service) '
                    '(rate(http_request_duration_seconds_bucket{layer="microservice"}[5m])))'],
                   {"h": 8, "w": 12, "x": 0, "y": 13}, unit="s"),
        timeseries(8, "服务间调用成功率",
                   ['sum by (caller, target) '
                    '(rate(downstream_requests_total{status!~"5..|timeout|unreachable|error"}[5m]))'
                    ' / sum by (caller, target) (rate(downstream_requests_total[5m]))'],
                   {"h": 8, "w": 12, "x": 12, "y": 13}, unit="percentunit",
                   legend="{{caller}} → {{target}}"),

        table(9, "采集目标健康明细",
              'up{layer=~"microservice|gateway|middleware"}',
              {"h": 7, "w": 24, "x": 0, "y": 21}),
    ],
)

# =========================================================================== #
# 大屏 2：业务总览
# =========================================================================== #
business_overview = dashboard(
    uid="yunqi-business-overview",
    title="云启商城 · 业务总览",
    description="订单、成交额、支付成功率、在线用户等核心业务指标",
    tags=["yunqi-mall", "business"],
    panels=[
        stat(1, "累计成功订单", 'sum(orders_total{status="success"})',
             {"h": 5, "w": 6, "x": 0, "y": 0}, unit="short", decimals=0,
             graph="area"),
        stat(2, "累计成交额 GMV", 'sum(order_amount_yuan_total)',
             {"h": 5, "w": 6, "x": 6, "y": 0}, unit="currencyCNY",
             decimals=2, graph="area"),
        stat(3, "支付成功率（近 10 分钟）",
             'sum(rate(payment_callbacks_total{status="success"}[10m]))'
             ' / sum(rate(payment_callbacks_total[10m]))',
             {"h": 5, "w": 6, "x": 12, "y": 0}, unit="percentunit",
             thresholds=[{"color": "red", "value": None},
                         {"color": "yellow", "value": 0.95},
                         {"color": "green", "value": 0.98}]),
        stat(4, "当前在线用户", 'sum(active_users)',
             {"h": 5, "w": 6, "x": 18, "y": 0}, unit="short", decimals=0,
             graph="area"),

        timeseries(5, "每分钟订单量",
                   ['sum(increase(orders_total{status="success"}[1m]))',
                    'sum(increase(orders_total{status="failed"}[1m]))'],
                   {"h": 8, "w": 12, "x": 0, "y": 5}, unit="short",
                   legend=["下单成功", "下单失败"], fill=15),
        timeseries(6, "每分钟支付量",
                   ['sum(increase(payment_callbacks_total{status="success"}[1m]))',
                    'sum(increase(payment_callbacks_total{status="failed"}[1m]))'],
                   {"h": 8, "w": 12, "x": 12, "y": 5}, unit="short",
                   legend=["支付成功", "支付失败"], fill=15),
        timeseries(7, "在线用户数趋势", ['sum(active_users)'],
                   {"h": 8, "w": 12, "x": 0, "y": 13}, unit="short",
                   legend="在线用户", fill=20),
        timeseries(8, "成交额增长速率", ['sum(rate(order_amount_yuan_total[5m])) * 60'],
                   {"h": 8, "w": 12, "x": 12, "y": 13},
                   unit="currencyCNY", legend="每分钟成交额（元）", fill=15),

        timeseries(9, "登录成功率",
                   ['sum(rate(logins_total{status="success"}[5m]))'
                    ' / sum(rate(logins_total[5m]))'],
                   {"h": 7, "w": 12, "x": 0, "y": 21}, unit="percentunit",
                   legend="登录成功率"),
        timeseries(10, "支付渠道分布",
                   ['sum by (channel) (increase(payment_channel_total[5m]))'],
                   {"h": 7, "w": 12, "x": 12, "y": 21}, unit="short",
                   legend="{{channel}}", stacking="normal"),
    ],
)

# =========================================================================== #
# 大屏 3：中间件总览
# =========================================================================== #
middleware_overview = dashboard(
    uid="yunqi-middleware-overview",
    title="云启商城 · 中间件与网关",
    description="MySQL、Redis、Nginx 网关的运行状态与性能指标",
    tags=["yunqi-mall", "middleware", "infra"],
    panels=[
        stat(1, "MySQL 状态", 'up{service="mysql"}',
             {"h": 5, "w": 6, "x": 0, "y": 0}, unit="short", decimals=0,
             mappings=UP_DOWN_MAPPING,
             thresholds=[{"color": "red", "value": None},
                         {"color": "green", "value": 1}]),
        stat(2, "Redis 状态", 'up{service="redis"}',
             {"h": 5, "w": 6, "x": 6, "y": 0}, unit="short", decimals=0,
             mappings=UP_DOWN_MAPPING,
             thresholds=[{"color": "red", "value": None},
                         {"color": "green", "value": 1}]),
        stat(3, "网关活跃连接", 'nginx_connections_active',
             {"h": 5, "w": 6, "x": 12, "y": 0}, unit="short", decimals=0,
             graph="area"),
        stat(4, "Redis 内存使用率",
             'redis_memory_used_bytes / redis_memory_max_bytes',
             {"h": 5, "w": 6, "x": 18, "y": 0}, unit="percentunit",
             thresholds=[{"color": "green", "value": None},
                         {"color": "yellow", "value": 0.7},
                         {"color": "red", "value": 0.85}]),

        timeseries(5, "MySQL QPS", ['rate(mysql_global_status_queries[1m])'],
                   {"h": 8, "w": 12, "x": 0, "y": 5}, unit="reqps",
                   legend="MySQL 查询速率", fill=15),
        timeseries(6, "MySQL 连接与慢查询",
                   ['mysql_global_status_threads_connected',
                    'rate(mysql_global_status_slow_queries[5m]) * 60'],
                   {"h": 8, "w": 12, "x": 12, "y": 5}, unit="short",
                   legend=["当前连接数", "每分钟慢查询数"]),
        timeseries(7, "Redis 缓存命中率",
                   ['rate(redis_keyspace_hits_total[5m])'
                    ' / (rate(redis_keyspace_hits_total[5m])'
                    ' + rate(redis_keyspace_misses_total[5m]))'],
                   {"h": 8, "w": 12, "x": 0, "y": 13}, unit="percentunit",
                   legend="命中率"),
        timeseries(8, "Redis 命令处理速率",
                   ['rate(redis_commands_processed_total[1m])'],
                   {"h": 8, "w": 12, "x": 12, "y": 13}, unit="ops",
                   legend="每秒处理命令数", fill=15),

        timeseries(9, "网关请求速率", ['rate(nginx_http_requests_total[1m])'],
                   {"h": 7, "w": 12, "x": 0, "y": 21}, unit="reqps",
                   legend="Nginx 请求速率", fill=15),
        timeseries(10, "Redis 内存占用", ['redis_memory_used_bytes'],
                   {"h": 7, "w": 12, "x": 12, "y": 21}, unit="bytes",
                   legend="已用内存", fill=20),
    ],
)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    items = [
        ("01-service-overview.json", service_overview),
        ("02-business-overview.json", business_overview),
        ("03-middleware-overview.json", middleware_overview),
    ]
    for filename, payload in items:
        path = os.path.join(OUT_DIR, filename)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("生成 %-32s 面板数=%d" % (filename, len(payload["panels"])))


if __name__ == "__main__":
    main()
