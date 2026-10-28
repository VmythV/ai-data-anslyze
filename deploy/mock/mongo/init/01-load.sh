#!/bin/bash
# 客服工单库（MongoDB）：导入数据并创建只读账号
set -e
for c in tickets agents; do
  gunzip -c "/docker-entrypoint-initdb.d/$c.jsonl.gz" | mongoimport --quiet \
    -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin \
    --db service --collection "$c"
done
mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin <<'JS'
const db = db.getSiblingDB('service');
db.tickets.createIndex({ created_at: 1 });
db.tickets.createIndex({ order_no: 1 });
// 联邦查询（Trino）需要在 _schema 集合里存推断出的表结构：只给这一个集合写权限，业务数据仍然只读
db.createRole({
  role: 'trinoSchemaWriter',
  privileges: [{ resource: { db: 'service', collection: '_schema' },
                 actions: ['find', 'insert', 'update', 'remove', 'createCollection', 'createIndex'] }],
  roles: [],
});
db.createUser({ user: 'analyst', pwd: 'analyst_ro_2026', roles: [{ role: 'read', db: 'service' }, 'trinoSchemaWriter'] });
JS
