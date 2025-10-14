import pyarrow as pa
import pyarrow.parquet as pq
from skip_bronze import hmdb_entities
from silver_schema import get_entity_schema

# Central schema definition lives in silver_schema to avoid divergence.
schema = get_entity_schema()

# Stream entities in batches
batch_size = 10000
batch = []
total = 0

writer = None

for entity in hmdb_entities():
    batch.append(entity._asdict())

    if len(batch) >= batch_size:
        # Convert batch to PyArrow table
        table = pa.Table.from_pylist(batch, schema=schema)

        if writer is None:
            # Open writer on first batch
            writer = pq.ParquetWriter('hmdb_silver.parquet', schema)

        writer.write_table(table)
        total += len(batch)
        print(f"Processed {total} records...")
        batch = []

# Write remaining records
if batch:
    table = pa.Table.from_pylist(batch, schema=schema)
    if writer is None:
        writer = pq.ParquetWriter('hmdb_silver.parquet', schema)
    writer.write_table(table)
    total += len(batch)

# Close the writer
if writer:
    writer.close()

print(f"Wrote {total} records to hmdb_silver.parquet")
