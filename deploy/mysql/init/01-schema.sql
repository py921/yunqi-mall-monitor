-- =====================================================================
-- 云启商城（YunQi Mall）数据库初始化
-- 仅在 mysql 数据卷为空时由 docker-entrypoint-initdb.d 执行一次
-- 如需重新初始化：docker compose down -v && docker compose up -d
-- =====================================================================

CREATE DATABASE IF NOT EXISTS yunqi_mall
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE yunqi_mall;

-- ---------------------------------------------------------------------
-- 用户表
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS t_user (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username      VARCHAR(64)     NOT NULL,
    phone         VARCHAR(20)     DEFAULT NULL,
    password_hash CHAR(64)        NOT NULL,
    level         TINYINT         NOT NULL DEFAULT 1 COMMENT '1普通 2黄金 3钻石',
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_username (username),
    KEY idx_created_at (created_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT '用户表';

-- ---------------------------------------------------------------------
-- 商品表
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS t_product (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name       VARCHAR(128)    NOT NULL,
    category   VARCHAR(32)     NOT NULL,
    price_yuan DECIMAL(10, 2)  NOT NULL,
    stock      INT             NOT NULL DEFAULT 0,
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_category (category)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT '商品表';

-- ---------------------------------------------------------------------
-- 订单表
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS t_order (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    order_no    VARCHAR(32)     NOT NULL,
    user_id     BIGINT UNSIGNED NOT NULL,
    product_id  BIGINT UNSIGNED NOT NULL,
    quantity    INT             NOT NULL DEFAULT 1,
    amount_yuan DECIMAL(10, 2)  NOT NULL,
    status      VARCHAR(16)     NOT NULL DEFAULT 'CREATED'
                COMMENT 'CREATED/PAID/CANCELLED/REFUNDED',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_order_no (order_no),
    KEY idx_user_id (user_id),
    KEY idx_created_at (created_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT '订单表';

-- ---------------------------------------------------------------------
-- 支付单表
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS t_payment (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    pay_no      VARCHAR(32)     NOT NULL,
    order_no    VARCHAR(32)     NOT NULL,
    user_id     BIGINT UNSIGNED DEFAULT NULL,
    amount_yuan DECIMAL(10, 2)  NOT NULL,
    channel     VARCHAR(16)     NOT NULL COMMENT 'ALIPAY/WECHAT/UNIONPAY',
    status      VARCHAR(16)     NOT NULL DEFAULT 'PENDING'
                COMMENT 'PENDING/SUCCESS/FAILED',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_pay_no (pay_no),
    KEY idx_order_no (order_no),
    KEY idx_created_at (created_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT '支付单表';

-- ---------------------------------------------------------------------
-- 种子数据：200 个用户（统一初始密码 Mall@123456）
-- 用交叉连接生成序列，兼容 MySQL 5.7 / 8.x
-- ---------------------------------------------------------------------
INSERT INTO t_user (username, phone, password_hash, level)
SELECT CONCAT('yunqi_user_', LPAD(nums.n, 4, '0')),
       CONCAT('138', LPAD(nums.n, 8, '0')),
       SHA2('Mall@123456', 256),
       1 + (nums.n % 3)
-- 注意：位数必须真的覆盖到 200。只有 a.i / b.i 两个十位集合时，
-- 实际只能生成 9 + 90 + 1 = 100 个用户，而压测用户池是 200，
-- 会导致一半的登录与用户查询打到不存在的用户上（401 / 404）。
FROM (
    SELECT a.i + b.i * 10 + c.i * 100 + 1 AS n
    FROM (SELECT 0 AS i UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
          UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) a
    CROSS JOIN (SELECT 0 AS i UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
          UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) b
    CROSS JOIN (SELECT 0 AS i UNION SELECT 1) c
) nums
WHERE nums.n <= 200;

-- ---------------------------------------------------------------------
-- 种子数据：20 个商品
-- ---------------------------------------------------------------------
INSERT INTO t_product (name, category, price_yuan, stock) VALUES
('云启智能音箱 Pro',        '数码配件',  399.00, 1200),
('云启降噪蓝牙耳机 Air',     '数码配件',  699.00,  860),
('云启 27 英寸 2K 显示器',   '电脑外设', 1299.00,  430),
('云启机械键盘 87 键',       '电脑外设',  459.00,  980),
('云启无线鼠标 静音版',       '电脑外设',  129.00, 2400),
('云启移动电源 20000mAh',    '数码配件',  199.00, 1650),
('云启智能手环 5 代',        '智能穿戴',  249.00, 1320),
('云启扫地机器人 X1',        '智能家居', 2399.00,  180),
('云启空气净化器 Max',       '智能家居', 1099.00,  260),
('云启电饭煲 4L 智能版',     '厨房家电',  399.00,  540),
('云启破壁料理机',           '厨房家电',  599.00,  380),
('云启电动牙刷 声波款',       '个护健康',  269.00,  910),
('云启筋膜枪 mini',          '个护健康',  349.00,  620),
('云启保温杯 316 不锈钢',     '生活用品',   89.00, 3200),
('云启双肩背包 商务款',       '箱包服饰',  259.00,  740),
('云启运动跑鞋 轻量款',       '箱包服饰',  429.00,  560),
('云启护眼台灯 国AA级',       '家居照明',  199.00, 1180),
('云启除螨仪 手持款',         '家居清洁',  329.00,  450),
('云启车载充电器 65W',        '数码配件',   99.00, 2100),
('云启智能门锁 C 级锁芯',     '智能家居', 1299.00,  210);

-- ---------------------------------------------------------------------
-- mysqld-exporter 采集账号（最小权限原则，不给写权限）
-- 使用 mysql_native_password 以兼容 exporter 的驱动
-- ---------------------------------------------------------------------
CREATE USER IF NOT EXISTS 'exporter'@'%'
    IDENTIFIED WITH mysql_native_password BY 'Exporter@2024'
    WITH MAX_USER_CONNECTIONS 5;

GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'exporter'@'%';

FLUSH PRIVILEGES;
