import type { DataSourceKind } from './types'

export const KINDS: { value: DataSourceKind; label: string; port: number; short: string }[] = [
  { value: 'mysql', label: 'MySQL', port: 3306, short: 'MySQL' },
  { value: 'postgresql', label: 'PostgreSQL', port: 5432, short: 'PG' },
  { value: 'clickhouse', label: 'ClickHouse', port: 8123, short: 'CH' },
  { value: 'mongodb', label: 'MongoDB', port: 27017, short: 'Mongo' },
]

export const kindLabel = (kind: string) => KINDS.find((k) => k.value === kind)?.label ?? kind
export const kindShort = (kind: string) => KINDS.find((k) => k.value === kind)?.short ?? kind
