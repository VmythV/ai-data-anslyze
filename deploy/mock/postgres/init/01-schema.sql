-- 会员营销库（PostgreSQL）：public 放会员，marketing 放营销活动
CREATE SCHEMA marketing;

CREATE TABLE public.members (
  member_id VARCHAR(16) PRIMARY KEY,
  shop_customer_id INTEGER UNIQUE,
  phone_tail CHAR(4) NOT NULL,
  joined_at TIMESTAMP NOT NULL,
  tier VARCHAR(8) NOT NULL,
  source VARCHAR(16) NOT NULL,
  marketing_opt_in BOOLEAN NOT NULL
);
COMMENT ON TABLE public.members IS '会员档案';
COMMENT ON COLUMN public.members.member_id IS '会员号：M + 8 位数字。电商注册用户为 M + 8 位 shop 客户ID';
COMMENT ON COLUMN public.members.shop_customer_id IS '对应的电商客户ID（shop.customers.id），未在电商注册的会员为空';
COMMENT ON COLUMN public.members.phone_tail IS '手机号后四位';
COMMENT ON COLUMN public.members.joined_at IS '入会时间';
COMMENT ON COLUMN public.members.tier IS '会员等级：普通/银卡/金卡/黑金';
COMMENT ON COLUMN public.members.source IS '入会来源';
COMMENT ON COLUMN public.members.marketing_opt_in IS '是否同意接收营销信息';

CREATE TABLE public.points_ledger (
  id INTEGER PRIMARY KEY,
  member_id VARCHAR(16) NOT NULL,
  occurred_at TIMESTAMP NOT NULL,
  points INTEGER NOT NULL,
  reason VARCHAR(16) NOT NULL,
  ref_order_no VARCHAR(24)
);
CREATE INDEX ON public.points_ledger (member_id);
COMMENT ON TABLE public.points_ledger IS '积分流水';
COMMENT ON COLUMN public.points_ledger.points IS '积分变动，正数为获得，负数为消耗';
COMMENT ON COLUMN public.points_ledger.reason IS '变动原因：消费积分/积分兑换';
COMMENT ON COLUMN public.points_ledger.ref_order_no IS '关联的电商订单编号（shop.orders.order_no）';

CREATE TABLE marketing.campaigns (
  id INTEGER PRIMARY KEY,
  name VARCHAR(32) NOT NULL,
  channel VARCHAR(8) NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  budget NUMERIC(12,2) NOT NULL
);
COMMENT ON TABLE marketing.campaigns IS '营销活动';
COMMENT ON COLUMN marketing.campaigns.budget IS '活动预算（元）';

CREATE TABLE marketing.coupons (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL,
  member_id VARCHAR(16) NOT NULL,
  face_value NUMERIC(8,2) NOT NULL,
  threshold NUMERIC(8,2) NOT NULL,
  issued_at TIMESTAMP NOT NULL,
  expire_at TIMESTAMP NOT NULL
);
CREATE INDEX ON marketing.coupons (campaign_id);
COMMENT ON TABLE marketing.coupons IS '优惠券发放记录';
COMMENT ON COLUMN marketing.coupons.face_value IS '面额（元）';
COMMENT ON COLUMN marketing.coupons.threshold IS '使用门槛（满多少元可用）';

CREATE TABLE marketing.coupon_redemptions (
  coupon_id INTEGER PRIMARY KEY,
  order_no VARCHAR(24) NOT NULL,
  redeemed_at TIMESTAMP NOT NULL,
  discount NUMERIC(8,2) NOT NULL
);
COMMENT ON TABLE marketing.coupon_redemptions IS '优惠券核销记录';
COMMENT ON COLUMN marketing.coupon_redemptions.order_no IS '使用该券的电商订单编号（shop.orders.order_no）';
COMMENT ON COLUMN marketing.coupon_redemptions.discount IS '实际抵扣金额（元）';

CREATE ROLE analyst LOGIN PASSWORD 'analyst_ro_2026';
ALTER ROLE analyst SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE crm TO analyst;
GRANT USAGE ON SCHEMA public, marketing TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public, marketing TO analyst;
