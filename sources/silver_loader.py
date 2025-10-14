import sys
import pyarrow as pa
import pyarrow.parquet as pq
from silver_schema import SILVER_ENTITY_SCHEMA, SILVER_CV_TERM_SCHEMA

__all__ = [
    'SOURCES',
    'main',
]

# Available sources - format: (module_name, function_name, schema_type)
SOURCES = {
    'hmdb': ('hmdb', 'hmdb_entities', 'entity'),
    'lipidmaps': ('lipidmaps', 'lipidmaps_lipids', 'entity'),
    'swisslipids': ('swisslipids', 'swisslipids_lipids', 'entity'),
    'psimi': ('psimi', 'psimi_ontology', 'cv_term'),
}

def main(source_name):
    if source_name not in SOURCES:
        print(f"Unknown source: {source_name}")
        print(f"Available sources: {', '.join(SOURCES.keys())}")
        sys.exit(1)

    module_name, func_name, schema_type = SOURCES[source_name]
    module = __import__(module_name)
    record_generator = getattr(module, func_name)

    # Central schema definition lives in silver_schema to avoid divergence.
    schema = SILVER_ENTITY_SCHEMA if schema_type == 'entity' else SILVER_CV_TERM_SCHEMA

    # Stream records in batches
    batch_size = 10000
    batch = []
    total = 0
    writer = None
    output_file = f'{source_name}_silver.parquet'

    for record in record_generator():
        batch.append(record._asdict())

        if len(batch) >= batch_size:
            # Convert batch to PyArrow table
            table = pa.Table.from_pylist(batch, schema=schema)

            if writer is None:
                # Open writer on first batch
                writer = pq.ParquetWriter(output_file, schema)

            writer.write_table(table)
            total += len(batch)
            print(f"Processed {total} records...")
            batch = []

    # Write remaining records
    if batch:
        table = pa.Table.from_pylist(batch, schema=schema)
        if writer is None:
            writer = pq.ParquetWriter(output_file, schema)
        writer.write_table(table)
        total += len(batch)

    # Close the writer
    if writer:
        writer.close()

    print(f"Wrote {total} records to {output_file}")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python silver_loader.py <source>")
        print(f"Available sources: {', '.join(SOURCES.keys())}")
        sys.exit(1)

    main(sys.argv[1])
