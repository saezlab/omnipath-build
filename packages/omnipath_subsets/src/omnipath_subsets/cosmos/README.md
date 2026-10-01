# COSMOS writes

`rebuild(conn, schema)` retains the caller's transaction and returns product
counts. Projection, alias selection, source-event ordering and reaction indexes
use the existing rules.

Writes use batches of at most 1,000 edges and 1,000 distinct pending label keys.
The label buffer retains the first occurrence of each `(entity_id,
wanted_namespace)`; repeats after a flush use the existing primary key and
`ON CONFLICT DO NOTHING`. There is no release-wide Python seen set. Each
`executemany` call finishes its automatic pipeline before another server-cursor
FETCH, and final buffers flush before the product statistics are calculated.

Batches retain edge insertion order and generated edge IDs. Failed writes roll
back with the caller's transaction, including a rebuild's TRUNCATE and sequence
restart. Tables, indexes, query scope and scientific projection are unchanged.
