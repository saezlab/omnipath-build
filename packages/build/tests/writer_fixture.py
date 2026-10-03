"""Small fixture builds through the production flat-table writer."""

import tempfile
import pyarrow.parquet as pq
from omnipath_build.silver import SilverExtractor
from omnipath_build.writer import ParquetWriter


def write_observations(resolver, entities, relations, payloads):
    with tempfile.TemporaryDirectory() as directory:
        writer = ParquetWriter(directory, library_dir=resolver.library_dir)
        extractor = SilverExtractor("fixture", "test")
        extractor.entities, extractor.relations, extractor.payloads = entities, relations, payloads
        writer.append_observations(extractor, resolver)
        paths = writer.close()[:3]
        return [pq.read_table(p).to_pylist() for p in paths]
