import pytest

from data_analyze.datasources.sql_guard import UnsafeSqlError, check_readonly, with_row_limit


@pytest.mark.parametrize(
    "sql",
    [
        "select 1",
        "SELECT * FROM orders WHERE note LIKE '%退款%' AND t > '2025-01-01 10:00:00'",
        "with m as (select 1 x) select * from m",
        "select 1 union all select 2",
        "select a.* from (select id from orders) a join regions r on r.id = a.id",
    ],
)
def test_allows_reads(sql: str) -> None:
    check_readonly(sql, "mysql")


@pytest.mark.parametrize(
    "sql",
    [
        "delete from orders",
        "update orders set status = 'x'",
        "insert into orders values (1)",
        "drop table orders",
        "select 1; drop table orders",
        "select * from orders for update",
        "select sleep(10)",
        "select benchmark(100000000, md5('a'))",
        "select load_file('/etc/passwd')",
        "select * from orders into outfile '/tmp/x'",
        "show tables",
        "set global read_only = 0",
        "",
    ],
)
def test_rejects_unsafe(sql: str) -> None:
    with pytest.raises(UnsafeSqlError):
        check_readonly(sql, "mysql")


def test_row_limit() -> None:
    assert with_row_limit(check_readonly("select * from t", "mysql"), 100, "mysql").endswith("LIMIT 101")
    assert with_row_limit(check_readonly("select * from t limit 5", "mysql"), 100, "mysql").endswith("LIMIT 5")
    assert with_row_limit(check_readonly("select * from t limit 5000", "mysql"), 100, "mysql").endswith("LIMIT 101")
    assert "AS _q LIMIT 101" in with_row_limit(check_readonly("select 1 union select 2", "mysql"), 100, "mysql")
