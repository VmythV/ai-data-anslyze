-- 仓储物流库：与 shop 在同一个 MySQL 实例上，可以在一条 SQL 里跨库关联
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS wms DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE wms;

CREATE TABLE warehouses (
  id INT PRIMARY KEY COMMENT '仓库ID',
  code VARCHAR(16) NOT NULL COMMENT '仓库编码',
  name VARCHAR(32) NOT NULL COMMENT '仓库名称',
  region VARCHAR(16) NOT NULL COMMENT '所在大区',
  city VARCHAR(16) NOT NULL COMMENT '所在城市'
) COMMENT='仓库';

CREATE TABLE shipments (
  id INT PRIMARY KEY COMMENT '发货单ID',
  order_no VARCHAR(24) NOT NULL COMMENT '电商订单编号（对应 shop.orders.order_no）',
  warehouse_id INT NOT NULL COMMENT '发货仓库，关联 warehouses.id',
  carrier VARCHAR(8) NOT NULL COMMENT '承运商：顺丰/京东物流/中通/圆通/EMS',
  shipped_at DATETIME NOT NULL COMMENT '发货时间',
  delivered_at DATETIME NULL COMMENT '签收时间，未签收为空',
  status VARCHAR(8) NOT NULL COMMENT '物流状态：已签收/运输中/退回',
  freight_cost DECIMAL(8,2) NOT NULL COMMENT '运费成本（元）',
  KEY idx_order (order_no),
  KEY idx_shipped (shipped_at)
) COMMENT='发货单（每个订单一条）';

CREATE TABLE inventory_monthly (
  month DATE NOT NULL COMMENT '月份（每月 1 日）',
  warehouse_id INT NOT NULL COMMENT '仓库ID',
  product_id INT NOT NULL COMMENT '商品ID（对应 shop.products.id）',
  on_hand INT NOT NULL COMMENT '月末库存（件），负数表示超卖待补',
  inbound_qty INT NOT NULL COMMENT '当月入库（件）',
  outbound_qty INT NOT NULL COMMENT '当月出库（件）',
  PRIMARY KEY (month, warehouse_id, product_id)
) COMMENT='月度库存快照';

GRANT SELECT, SHOW VIEW ON wms.* TO 'analyst'@'%';
FLUSH PRIVILEGES;
