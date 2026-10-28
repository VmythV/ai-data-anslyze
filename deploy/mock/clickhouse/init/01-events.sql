-- 用户行为日志（ClickHouse）：在库内按规则生成约 400 万行，只覆盖 2025-01-01 之后
CREATE DATABASE IF NOT EXISTS events;

CREATE TABLE events.user_events
(
    event_date Date COMMENT '事件日期',
    event_time DateTime COMMENT '事件时间',
    user_id Nullable(UInt32) COMMENT '登录用户的电商客户ID（shop.customers.id），未登录为空',
    anonymous_id String COMMENT '设备匿名ID',
    session_id String COMMENT '会话ID',
    event_type LowCardinality(String) COMMENT '事件：page_view/search/product_view/add_to_cart/checkout/pay_success',
    product_id Nullable(UInt32) COMMENT '商品ID（shop.products.id），与商品无关的事件为空',
    channel LowCardinality(String) COMMENT '渠道：APP/小程序/PC官网',
    device LowCardinality(String) COMMENT '设备：iOS/Android/Windows/macOS',
    city LowCardinality(String) COMMENT '城市'
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_date, event_type, channel)
COMMENT '用户行为埋点日志（2025-01-01 起）';

-- 每个会话一行种子，再按漏斗展开成事件
INSERT INTO events.user_events
SELECT
    toDate(t) AS event_date,
    t AS event_time,
    if(logged, toUInt32(1 + cityHash64(s, 'u') % 18000), NULL) AS user_id,
    concat('anon_', toString(cityHash64(s, 'a') % 600000)) AS anonymous_id,
    concat('sess_', toString(s)) AS session_id,
    ['page_view', 'search', 'product_view', 'add_to_cart', 'checkout', 'pay_success'][step] AS event_type,
    if(step >= 3, toUInt32(1 + cityHash64(s, 'p') % 201), NULL) AS product_id,
    ch AS channel,
    multiIf(ch = 'PC官网', ['Windows', 'macOS'][1 + cityHash64(s, 'd') % 2], ['iOS', 'Android'][1 + cityHash64(s, 'd') % 2]) AS device,
    ['上海', '杭州', '广州', '深圳', '北京', '成都', '武汉', '南京', '西安', '沈阳', '重庆', '苏州'][1 + cityHash64(s, 'c') % 12] AS city
FROM
(
    SELECT
        number AS s,
        toDateTime('2025-01-01 00:00:00') + toIntervalSecond(cityHash64(number, 't') % (664 * 86400)) AS start,
        (cityHash64(number, 'l') % 100) < 55 AS logged,
        ['APP', 'APP', '小程序', '小程序', 'PC官网'][1 + cityHash64(number, 'ch') % 5] AS ch,
        -- 漏斗深度：浏览 → 搜索 → 看商品 → 加购 8% → 下单 50% → 支付 80%
        multiIf(
            (cityHash64(number, 'f') % 10000) < 320, 6,
            (cityHash64(number, 'f') % 10000) < 400, 5,
            (cityHash64(number, 'f') % 10000) < 800, 4,
            (cityHash64(number, 'f') % 10000) < 6000, 3,
            (cityHash64(number, 'f') % 10000) < 8000, 2,
            1) AS depth
    FROM numbers(1200000)
)
ARRAY JOIN range(1, depth + 1) AS step
ARRAY JOIN [start + toIntervalSecond(step * 37)] AS t;

CREATE USER IF NOT EXISTS analyst IDENTIFIED WITH plaintext_password BY 'analyst_ro_2026' SETTINGS readonly = 2;
GRANT SELECT ON events.* TO analyst;
