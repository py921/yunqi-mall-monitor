# 所有反向代理 location 共用的头部与超时设置
proxy_http_version 1.1;
proxy_set_header   Connection         "";
proxy_set_header   Host               $host;
proxy_set_header   X-Real-IP          $remote_addr;
proxy_set_header   X-Forwarded-For    $proxy_add_x_forwarded_for;
proxy_set_header   X-Forwarded-Proto  $scheme;
proxy_set_header   X-Request-Id       $request_id;

proxy_connect_timeout  3s;
proxy_send_timeout     10s;
proxy_read_timeout     10s;

proxy_next_upstream    error timeout http_502 http_503 http_504;
