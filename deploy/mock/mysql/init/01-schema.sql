-- 电商场景 mock 库：表与字段都带中文注释，AI 依赖这些注释理解业务口径
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS shop DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE shop;

CREATE TABLE regions (
  id INT PRIMARY KEY COMMENT '地区ID',
  region VARCHAR(16) NOT NULL COMMENT '大区：华东/华南/华北/华中/西南/西北/东北',
  province VARCHAR(16) NOT NULL COMMENT '省份',
  city VARCHAR(16) NOT NULL COMMENT '城市'
) COMMENT='地区维表（城市粒度）';

CREATE TABLE categories (
  id INT PRIMARY KEY COMMENT '品类ID',
  name VARCHAR(32) NOT NULL COMMENT '品类名称',
  parent_id INT NULL COMMENT '上级品类ID，一级品类为空',
  level TINYINT NOT NULL COMMENT '层级：1=一级品类，2=二级品类'
) COMMENT='商品品类（两级）';

CREATE TABLE products (
  id INT PRIMARY KEY COMMENT '商品ID',
  name VARCHAR(64) NOT NULL COMMENT '商品名称',
  category_id INT NOT NULL COMMENT '二级品类ID，关联 categories.id',
  brand VARCHAR(32) NOT NULL COMMENT '品牌',
  unit_price DECIMAL(10,2) NOT NULL COMMENT '标价（元）',
  cost_price DECIMAL(10,2) NOT NULL COMMENT '成本价（元）',
  launch_date DATE NOT NULL COMMENT '上架日期',
  status VARCHAR(8) NOT NULL COMMENT '状态：在售/下架',
  KEY idx_category (category_id)
) COMMENT='商品';

CREATE TABLE customers (
  id INT PRIMARY KEY COMMENT '客户ID',
  name VARCHAR(16) NOT NULL COMMENT '客户姓名',
  gender CHAR(1) NOT NULL COMMENT '性别：男/女',
  birth_year SMALLINT NOT NULL COMMENT '出生年份',
  region_id INT NOT NULL COMMENT '常住地区ID，关联 regions.id',
  register_date DATE NOT NULL COMMENT '注册日期',
  member_level VARCHAR(8) NOT NULL COMMENT '会员等级：普通会员/银卡会员/金卡会员/钻石会员',
  register_channel VARCHAR(8) NOT NULL COMMENT '注册渠道：APP/小程序/PC官网/线下门店',
  KEY idx_region (region_id)
) COMMENT='客户';

CREATE TABLE orders (
  id INT PRIMARY KEY COMMENT '订单ID',
  order_no VARCHAR(24) NOT NULL COMMENT '订单编号',
  customer_id INT NOT NULL COMMENT '客户ID，关联 customers.id',
  order_time DATETIME NOT NULL COMMENT '下单时间',
  status VARCHAR(8) NOT NULL COMMENT '订单状态：已完成/已取消/已退款/配送中。统计销售额时通常只算已完成与配送中',
  pay_method VARCHAR(8) NOT NULL COMMENT '支付方式：微信支付/支付宝/银行卡/货到付款',
  channel VARCHAR(8) NOT NULL COMMENT '下单渠道：APP/小程序/PC官网/线下门店',
  region_id INT NOT NULL COMMENT '收货地区ID，关联 regions.id',
  total_amount DECIMAL(12,2) NOT NULL COMMENT '商品总额（元）',
  discount_amount DECIMAL(12,2) NOT NULL COMMENT '优惠金额（元）',
  shipping_fee DECIMAL(8,2) NOT NULL COMMENT '运费（元）',
  pay_amount DECIMAL(12,2) NOT NULL COMMENT '实付金额（元）= 商品总额 - 优惠 + 运费，GMV 口径用它',
  KEY idx_time (order_time),
  KEY idx_customer (customer_id),
  KEY idx_region (region_id)
) COMMENT='订单';

CREATE TABLE order_items (
  id INT PRIMARY KEY COMMENT '明细ID',
  order_id INT NOT NULL COMMENT '订单ID，关联 orders.id',
  product_id INT NOT NULL COMMENT '商品ID，关联 products.id',
  quantity INT NOT NULL COMMENT '购买数量',
  unit_price DECIMAL(10,2) NOT NULL COMMENT '成交单价（元）',
  amount DECIMAL(12,2) NOT NULL COMMENT '明细金额（元）= 单价 × 数量（未分摊订单优惠）',
  KEY idx_order (order_id),
  KEY idx_product (product_id)
) COMMENT='订单明细';

CREATE TABLE refunds (
  id INT PRIMARY KEY COMMENT '退款ID',
  order_id INT NOT NULL COMMENT '订单ID，关联 orders.id',
  refund_time DATETIME NOT NULL COMMENT '退款时间',
  refund_amount DECIMAL(12,2) NOT NULL COMMENT '退款金额（元）',
  reason VARCHAR(16) NOT NULL COMMENT '退款原因',
  KEY idx_order (order_id)
) COMMENT='退款记录';

CREATE TABLE daily_traffic (
  stat_date DATE NOT NULL COMMENT '统计日期',
  channel VARCHAR(8) NOT NULL COMMENT '流量渠道：APP/小程序/PC官网',
  visits INT NOT NULL COMMENT '访问次数（PV）',
  unique_visitors INT NOT NULL COMMENT '访客数（UV）',
  orders INT NOT NULL COMMENT '该渠道当日下单数',
  PRIMARY KEY (stat_date, channel)
) COMMENT='每日流量统计（线上渠道）';

-- 给 AI 用的只读账号
CREATE USER IF NOT EXISTS 'analyst'@'%' IDENTIFIED BY 'analyst_ro_2026';
GRANT SELECT, SHOW VIEW ON shop.* TO 'analyst'@'%';
FLUSH PRIVILEGES;
