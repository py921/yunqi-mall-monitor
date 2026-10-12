#!/usr/bin/env python3
"""项目配置一致性静态校验（无需 Docker 环境）。

多容器项目最容易翻车的不是代码逻辑，而是"配置之间对不上"，
比如 PromQL 引用了服务根本没暴露的指标名、dashboard 引用的数据源 uid 和
provisioning 里定义的不一致、targets 里写了 compose 中不存在的服务名。
这些错误在运行时才暴露，且往往只表现为一个空图或 No data。

本脚本把这些检查前移到提交前：

    python3 tools/verify_project.py

检查项：
  1. Python / YAML / JSON 语法
  2. PromQL 引用的指标名是否真的被某个服务暴露
  3. Grafana 数据源 uid 是否与 dashboard 引用一致
  4. Prometheus targets 里的主机名是否都是 compose 中定义的服务
  5. Grafana dashboard uid 是否唯一、面板 id 是否唯一
"""
import glob
import json
import os
import re
import sys

import yaml

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# PromQL 函数与关键字，不属于指标名
PROMQL_KEYWORDS = {
    "sum", "avg", "min", "max", "count", "stddev", "stdvar", "topk", "bottomk",
    "quantile", "count_values", "rate", "irate", "increase", "delta", "idelta",
    "deriv", "predict_linear", "resets", "changes", "histogram_quantile",
    "label_replace", "label_join", "abs", "ceil", "floor", "exp", "ln", "log2",
    "log10", "sqrt", "round", "clamp_max", "clamp_min", "sort", "sort_desc",
    "time", "timestamp", "vector", "scalar", "absent", "absent_over_time",
    "avg_over_time", "max_over_time", "min_over_time", "sum_over_time",
    "count_over_time", "quantile_over_time", "stddev_over_time",
    "last_over_time", "present_over_time", "by", "without", "on", "ignoring",
    "group_left", "group_right", "bool", "offset", "and", "or", "unless",
    "le", "granularity", "first_over_time", "double_exponential_smoothing",
}

# 标签名，出现在 by(...) 或 {} 中
LABEL_NAMES = {
    "service", "layer", "env", "team", "status", "caller", "target", "endpoint",
    "method", "instance", "job", "channel", "reason", "severity", "alertname",
    "category", "product_id", "quantile", "version", "type", "name", "code",
}

# 由 exporter / Prometheus 自身提供，不在本仓库代码里定义
EXTERNAL_METRICS = {
    "up", "scrape_duration_seconds", "scrape_samples_scraped",
    "mysql_global_status_queries", "mysql_global_status_slow_queries",
    "mysql_global_status_threads_connected",
    "mysql_global_variables_max_connections",
    "mysql_up", "mysql_global_status_uptime",
    "redis_memory_used_bytes", "redis_memory_max_bytes",
    "redis_keyspace_hits_total", "redis_keyspace_misses_total",
    "redis_commands_processed_total", "redis_connected_clients", "redis_up",
    "nginx_up", "nginx_connections_active", "nginx_connections_accepted",
    "nginx_http_requests_total",
    "prometheus_tsdb_head_series",
}

METRIC_DEF_RE = re.compile(
    r"(?:Counter|Histogram|Gauge|Summary)\(\s*[\"']([a-zA-Z_:][a-zA-Z0-9_:]*)[\"']"
)

errors: list[str] = []
warnings: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/")


# --------------------------------------------------------------------------- #
# 1. 语法检查
# --------------------------------------------------------------------------- #
def collect_python_metrics() -> set[str]:
    metrics: set[str] = set()
    for path in glob.glob(os.path.join(ROOT, "services", "**", "*.py"), recursive=True):
        src = open(path, encoding="utf-8").read()
        try:
            compile(src, path, "exec")
        except SyntaxError as exc:
            fail(f"Python 语法错误 {rel(path)}:{exc.lineno} {exc.msg}")
            continue
        for name in METRIC_DEF_RE.findall(src):
            metrics.add(name)
    for path in glob.glob(os.path.join(ROOT, "traffic-generator", "*.py")):
        try:
            compile(open(path, encoding="utf-8").read(), path, "exec")
        except SyntaxError as exc:
            fail(f"Python 语法错误 {rel(path)}:{exc.lineno} {exc.msg}")
    return metrics


def check_yaml_json() -> None:
    for pattern in ("*.yml", "*.yaml", "prometheus/**/*.yml", "prometheus/**/*.yaml",
                    "deploy/**/*.yml", "deploy/**/*.yaml", "grafana/**/*.yml",
                    "grafana/**/*.yaml"):
        for path in glob.glob(os.path.join(ROOT, pattern), recursive=True):
            try:
                yaml.safe_load(open(path, encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                fail(f"YAML 解析失败 {rel(path)}: {exc}")

    for pattern in ("prometheus/targets/*.json", "grafana/dashboards/*.json"):
        for path in glob.glob(os.path.join(ROOT, pattern)):
            try:
                json.load(open(path, encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                fail(f"JSON 解析失败 {rel(path)}: {exc}")


# --------------------------------------------------------------------------- #
# 2. PromQL 指标名一致性
# --------------------------------------------------------------------------- #
def extract_metrics(expr: str) -> set[str]:
    cleaned = re.sub(r'"[^"]*"', '""', expr)      # 去掉字符串字面量
    cleaned = re.sub(r"'.*?'", "''", cleaned)
    idents = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", cleaned))
    return {
        i for i in idents
        if i not in PROMQL_KEYWORDS and i not in LABEL_NAMES
    }


def check_promql(known: set[str]) -> None:
    known_full = set(known)
    for base in list(known):
        known_full.update({f"{base}_bucket", f"{base}_sum", f"{base}_count"})
    known_full |= EXTERNAL_METRICS

    seen: set[str] = set()

    for path in glob.glob(os.path.join(ROOT, "prometheus", "rules", "*.yml")):
        doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
        for group in doc.get("groups", []):
            for rule in group.get("rules", []):
                for name in extract_metrics(rule.get("expr", "")):
                    if name not in known_full:
                        seen.add(f"{rel(path)} → {rule.get('alert')} 引用了未知指标 `{name}`")

    for path in glob.glob(os.path.join(ROOT, "grafana", "dashboards", "*.json")):
        doc = json.load(open(path, encoding="utf-8"))
        for panel in doc.get("panels", []):
            for target in panel.get("targets", []):
                for name in extract_metrics(target.get("expr", "")):
                    if name not in known_full:
                        seen.add(f"{rel(path)} → 面板「{panel.get('title')}」"
                                 f"引用了未知指标 `{name}`")
    for item in sorted(seen):
        fail(item)


# --------------------------------------------------------------------------- #
# 3. Grafana 数据源 uid 一致性
# --------------------------------------------------------------------------- #
def check_grafana_datasource() -> None:
    ds_dir = os.path.join(ROOT, "grafana", "provisioning", "datasources")
    uids = set()
    for path in glob.glob(os.path.join(ds_dir, "*.yml")):
        doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
        for ds in doc.get("datasources", []):
            if ds.get("uid"):
                uids.add(ds["uid"])
    if not uids:
        fail("未在 grafana/provisioning/datasources 中找到带 uid 的数据源")

    dash_uids: list[str] = []
    for path in glob.glob(os.path.join(ROOT, "grafana", "dashboards", "*.json")):
        doc = json.load(open(path, encoding="utf-8"))
        uid = doc.get("uid")
        if not uid:
            fail(f"{rel(path)} 缺少 uid")
        dash_uids.append(uid)
        referenced = set()

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "prometheus" and node.get("uid"):
                    referenced.add(node["uid"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(doc)
        for uid in referenced:
            if uid not in uids:
                fail(f"{rel(path)} 引用了未定义的数据源 uid `{uid}`（可用：{sorted(uids)}）")

    if len(dash_uids) != len(set(dash_uids)):
        fail("存在重复的 dashboard uid")


# --------------------------------------------------------------------------- #
# 4. targets 主机名 vs compose 服务名
# --------------------------------------------------------------------------- #
def check_targets() -> None:
    compose_path = os.path.join(ROOT, "docker-compose.yml")
    doc = yaml.safe_load(open(compose_path, encoding="utf-8")) or {}
    services = set(doc.get("services", {}).keys())
    if not services:
        fail("docker-compose.yml 中未解析到任何服务")
        return

    allowed_hosts = services | {"localhost", "127.0.0.1", "host.docker.internal"}

    # 各组件实际监听 / exporter 默认暴露的端口
    expected_ports = {
        "order-service": "8001",
        "pay-service": "8002",
        "user-service": "8003",
        "mysqld-exporter": "9104",
        "redis-exporter": "9121",
        "nginx-exporter": "9113",
        "prometheus": "9090",
        "alertmanager": "9093",
    }

    for path in glob.glob(os.path.join(ROOT, "prometheus", "targets", "*.json")):
        for entry in json.load(open(path, encoding="utf-8")):
            for target in entry.get("targets", []):
                host, _, port = target.partition(":")
                if host not in allowed_hosts:
                    fail(f"{rel(path)} 中的主机 `{host}` 不是 docker-compose 里定义的服务")
                elif host in expected_ports and port != expected_ports[host]:
                    fail(f"{rel(path)} 中 {host} 的端口 {port} 与预期 "
                         f"{expected_ports[host]} 不一致")
                if not entry.get("labels", {}).get("service"):
                    fail(f"{rel(path)} 中 {target} 缺少 service 标签")

    # 微服务监听的端口必须写在 gunicorn 启动参数里
    raw = open(compose_path, encoding="utf-8").read()
    for port in ("8001", "8002", "8003"):
        if f"0.0.0.0:{port}" not in raw:
            fail(f"compose 中未找到微服务监听端口 0.0.0.0:{port}")


# --------------------------------------------------------------------------- #
# 4b. 账号密码在多处配置之间必须一致
# --------------------------------------------------------------------------- #
def check_credentials_consistency() -> None:
    def read(*parts: str) -> str:
        return open(os.path.join(ROOT, *parts), encoding="utf-8").read()

    compose = read("docker-compose.yml")
    env = read(".env.example")
    schema = read("deploy", "mysql", "init", "01-schema.sql")
    db_py = read("services", "common", "db.py")
    user_py = read("services", "user_service", "app.py")
    gen_py = read("traffic-generator", "gen.py")

    checks = [
        ("MySQL exporter 密码 Exporter@2024", "Exporter@2024",
         [("deploy/mysql/init/01-schema.sql", schema),
          ("docker-compose.yml", compose),
          (".env.example", env)]),
        ("应用数据库密码 Mall@2024", "Mall@2024",
         [("docker-compose.yml", compose),
          (".env.example", env),
          ("services/common/db.py", db_py)]),
        ("数据库名 yunqi_mall", "yunqi_mall",
         [("deploy/mysql/init/01-schema.sql", schema),
          ("docker-compose.yml", compose),
          (".env.example", env),
          ("services/common/db.py", db_py)]),
        ("种子用户密码 Mall@123456", "Mall@123456",
         [("deploy/mysql/init/01-schema.sql", schema),
          ("services/user_service/app.py", user_py),
          ("traffic-generator/gen.py", gen_py)]),
        ("Grafana 数据源 uid yunqi-prometheus", "yunqi-prometheus",
         [("grafana/provisioning/datasources/prometheus.yml",
           read("grafana", "provisioning", "datasources", "prometheus.yml"))]),
    ]

    for label, value, places in checks:
        missing = [name for name, content in places if value not in content]
        if missing:
            fail(f"{label} 缺失于：{', '.join(missing)}")

    # 流量生成器的用户池规模必须和 SQL 里生成的种子用户数一致
    seed_count = re.search(r"WHERE\s+nums\.n\s*<=\s*(\d+)", schema)
    pool_default = re.search(r'USER_POOL",\s*"(\d+)"', gen_py)
    if seed_count and pool_default and seed_count.group(1) != pool_default.group(1):
        fail(f"SQL 生成 {seed_count.group(1)} 个种子用户，但 traffic-generator "
             f"默认用户池为 {pool_default.group(1)}，会出现大量 404 登录失败")


# --------------------------------------------------------------------------- #
# 5. dashboard 面板 id 唯一性
# --------------------------------------------------------------------------- #
def check_panel_ids() -> None:
    for path in glob.glob(os.path.join(ROOT, "grafana", "dashboards", "*.json")):
        doc = json.load(open(path, encoding="utf-8"))
        ids = [p.get("id") for p in doc.get("panels", [])]
        if len(ids) != len(set(ids)):
            fail(f"{rel(path)} 存在重复的面板 id")
        for panel in doc.get("panels", []):
            mode = (panel.get("fieldConfig", {}).get("defaults", {})
                    .get("color", {}).get("mode"))
            legal = {
                "fixed", "shades", "thresholds", "palette-classic",
                "palette-classic-by-name", "continuous-GrYlRd",
                "continuous-RdYlGr", "continuous-BlYlRd", "continuous-YlRd",
                "continuous-BlPu", "continuous-YlBl", "continuous-blues",
                "continuous-reds", "continuous-greens", "continuous-purples",
            }
            if mode not in legal:
                fail(f"{rel(path)} 面板「{panel.get('title')}」的 color.mode=`{mode}` 非法")


def main() -> int:
    metrics = collect_python_metrics()
    check_yaml_json()
    check_promql(metrics)
    check_grafana_datasource()
    check_targets()
    check_credentials_consistency()
    check_panel_ids()

    print("=" * 68)
    print("云启商城监控平台 —— 配置一致性校验")
    print("=" * 68)
    print(f"识别到服务自定义指标 {len(metrics)} 个：")
    for name in sorted(metrics):
        print(f"  - {name}")
    print()

    for item in warnings:
        print(f"[警告] {item}")
    for item in errors:
        print(f"[错误] {item}")

    print()
    if errors:
        print(f"校验未通过：{len(errors)} 个错误，{len(warnings)} 个警告")
        return 1
    print(f"校验通过：0 个错误，{len(warnings)} 个警告")
    return 0


if __name__ == "__main__":
    sys.exit(main())
