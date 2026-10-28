# Trino（联邦查询，可选）

`--profile federation` 时启用。平台以 `platform` 用户管理 catalog（每个数据源一个，名字 = 数据源标识），
以 `agent` 用户执行 AI 的联邦查询（只读，不能访问 system 目录）。Trino 只在内部网络上，不对外暴露端口。
