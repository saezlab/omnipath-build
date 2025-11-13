# Refactored build_local_tables for new Entity schema
from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable
from typing import Dict, List, Tuple, Optional
import logging
import polars as pl

__all__ = ["build_local_tables"]
logger = logging.getLogger(__name__)

# --- Helpers -----------------------------------------------------------------

def _iter_parquet_files(root: Path) -> Iterable[Path]:
    """Iterate through all parquet files in subdirectories."""
    for d in sorted(root.glob("*")):
        if d.is_dir():
            yield from sorted(d.glob("*.parquet"))


def _load_source_data(root: Path) -> Dict[str, List[Tuple[Path, pl.LazyFrame]]]:
    """Load all parquet files grouped by source."""
    out: Dict[str, List[Tuple[Path, pl.LazyFrame]]] = {}
    for p in _iter_parquet_files(root):
        out.setdefault(p.parent.name, []).append((p, pl.scan_parquet(str(p))))
    logger.info(f"Found {len(out)} sources")
    return out


class LocalTableBuilder:
    """Builds normalized local tables from Entity records."""
    
    def __init__(self, source_id: int):
        self.source_id = source_id
        self.next_entity_id = 1
        self.next_identifier_id = 1
        self.next_annotation_id = 1
        self.next_membership_id = 1
        self.next_membership_annotation_id = 1
        
        # Track entities we've already created to avoid duplicates
        self.entity_registry = {}  # (type, identifiers_hash) -> local_entity_id
        
    def process_entities(self, df: pl.DataFrame) -> Dict[str, pl.DataFrame]:
        """Process a dataframe of Entity records into normalized tables."""
        
        # Initialize result containers
        entities = []
        identifiers = []
        annotations = []
        memberships = []
        membership_annotations = []
        
        # Process each entity
        for row in df.iter_rows(named=True):
            # Create main entity record
            entity_id = self._create_entity(row, entities)
            
            # Process identifiers
            if row.get('identifiers'):
                self._process_identifiers(entity_id, row['identifiers'], identifiers)
            
            # Process annotations
            if row.get('annotations'):
                self._process_annotations(entity_id, row['annotations'], annotations)
            
            # Process membership relationships
            if row.get('membership'):
                self._process_memberships(
                    entity_id, row['membership'], 
                    entities, identifiers, annotations,
                    memberships, membership_annotations
                )
        
        # Convert lists to DataFrames
        return {
            'entity': pl.DataFrame(entities) if entities else pl.DataFrame(),
            'entity_identifier': pl.DataFrame(identifiers) if identifiers else pl.DataFrame(),
            'entity_annotation': pl.DataFrame(annotations) if annotations else pl.DataFrame(),
            'membership': pl.DataFrame(memberships) if memberships else pl.DataFrame(),
            'membership_annotation': pl.DataFrame(membership_annotations) if membership_annotations else pl.DataFrame(),
        }
    
    def _create_entity(self, row: dict, entities: list) -> int:
        """Create entity record and return its ID."""
        entity_id = self.next_entity_id
        self.next_entity_id += 1
        
        entities.append({
            'local_entity_id': entity_id,
            'entity_type': row['type'],
            'source_id': self.source_id,
        })
        
        return entity_id
    
    def _process_identifiers(self, entity_id: int, identifiers_data: list, identifiers: list):
        """Extract identifier records from entity."""
        if not identifiers_data:
            return
            
        for identifier in identifiers_data:
            identifiers.append({
                'local_entity_identifier_id': self.next_identifier_id,
                'local_entity_id': entity_id,
                'type_id': identifier.get('type'),
                'identifier': identifier.get('value'),
                'source_id': self.source_id,
            })
            self.next_identifier_id += 1
    
    def _process_annotations(self, entity_id: int, annotations_data: list, annotations: list):
        """Extract annotation records from entity."""
        if not annotations_data:
            return
            
        for annotation in annotations_data:
            annotations.append({
                'local_entity_annotation_id': self.next_annotation_id,
                'local_entity_id': entity_id,
                'annotation_id': annotation.get('term'),
                'annotation_value': str(annotation.get('value')) if annotation.get('value') is not None else None,
                'annotation_unit': annotation.get('units'),
                'source_id': self.source_id,
            })
            self.next_annotation_id += 1
    
    def _process_memberships(self, entity_id: int, memberships_data: list,
                           entities: list, identifiers: list, annotations: list,
                           memberships: list, membership_annotations: list):
        """Process membership relationships and extract member entities."""
        if not memberships_data:
            return
            
        for membership in memberships_data:
            member_data = membership.get('member')
            if not member_data:
                continue
                
            # Create entity record for the member
            member_id = self._create_entity(member_data, entities)
            
            # Process member's identifiers
            if member_data.get('identifiers'):
                self._process_identifiers(member_id, member_data['identifiers'], identifiers)
            
            # Process member's annotations (entity-level)
            if member_data.get('annotations'):
                self._process_annotations(member_id, member_data['annotations'], annotations)
            
            # Create membership relationship
            # is_parent flag determines direction:
            # - True: member is parent of entity (member_id -> entity_id)
            # - False: entity is parent of member (entity_id -> member_id)
            is_parent = membership.get('is_parent', False)
            
            membership_record = {
                'local_membership_id': self.next_membership_id,
                'parent_id': member_id if is_parent else entity_id,
                'member_id': entity_id if is_parent else member_id,
                'source_id': self.source_id,
            }
            memberships.append(membership_record)
            
            # Process membership-specific annotations
            if membership.get('annotations'):
                for annotation in membership['annotations']:
                    membership_annotations.append({
                        'local_membership_annotation_id': self.next_membership_annotation_id,
                        'local_membership_id': self.next_membership_id,
                        'annotation_id': annotation.get('term'),
                        'annotation_value': str(annotation.get('value')) if annotation.get('value') is not None else None,
                        'annotation_unit': annotation.get('units'),
                        'source_id': self.source_id,
                    })
                    self.next_membership_annotation_id += 1
            
            self.next_membership_id += 1
            
            # Recursively process member's memberships if they exist
            if member_data.get('membership'):
                self._process_memberships(
                    member_id, member_data['membership'],
                    entities, identifiers, annotations,
                    memberships, membership_annotations
                )


def _save_tables(tables: Dict[str, pl.DataFrame], output_dir: Path, source_name: str):
    """Save processed tables to parquet files."""
    for table_name, df in tables.items():
        if len(df) > 0:
            output_path = output_dir / f"local_{table_name}_{source_name}.parquet"
            df.write_parquet(output_path)
            logger.info(f"  Saved {table_name}: {len(df):,} records -> {output_path.name}")


# --- Main --------------------------------------------------------------------

def build_local_tables(
    data_root: Path,
    output_dir: Path,
    sources_df: pl.DataFrame,
):
    """
    Build local tables from Entity parquet files.
    
    Args:
        data_root: Root directory containing source subdirectories with parquet files
        output_dir: Output directory for local tables
        sources_df: DataFrame with source metadata (must have 'name' and 'id' columns)
    """
    data = _load_source_data(data_root)
    name2id = {r["name"]: r["id"] for r in sources_df.iter_rows(named=True)}
    
    # Create output directory
    local_tables_dir = output_dir / "local_tables"
    local_tables_dir.mkdir(parents=True, exist_ok=True)
    
    # Process each source
    for source_name, files in data.items():
        if source_name not in name2id:
            logger.warning(f"Source {source_name} not found in sources_df, skipping")
            continue
            
        source_id = name2id[source_name]
        
        logger.info("\n" + "="*70)
        logger.info(f"Processing source: {source_name} (id={source_id})")
        logger.info("="*70)
        
        # Initialize builder for this source
        builder = LocalTableBuilder(source_id)
        
        # Collect all tables for this source
        all_tables = {
            'entity': [],
            'entity_identifier': [],
            'entity_annotation': [],
            'membership': [],
            'membership_annotation': [],
        }
        
        # Process each file
        for file_path, lazy_frame in files:
            logger.info(f"  Processing {file_path.name}")
            
            # Collect the dataframe
            df = lazy_frame.collect()
            if len(df) == 0:
                logger.info(f"    Empty file, skipping")
                continue
                
            logger.info(f"    Found {len(df):,} entities")
            
            # Process entities and get normalized tables
            tables = builder.process_entities(df)
            
            # Accumulate results
            for table_name, table_df in tables.items():
                if len(table_df) > 0:
                    all_tables[table_name].append(table_df)
        
        # Combine all tables for this source
        final_tables = {}
        for table_name, table_list in all_tables.items():
            if table_list:
                final_tables[table_name] = pl.concat(table_list, how="diagonal_relaxed")
                logger.info(f"  Total {table_name}: {len(final_tables[table_name]):,} records")
        
        # Save tables
        if final_tables:
            _save_tables(final_tables, local_tables_dir, source_name)
        else:
            logger.warning(f"  No data to save for {source_name}")
        
        logger.info(f"Completed processing {source_name}")
    
    logger.info("\n" + "="*70)
    logger.info("Local table building complete!")
    logger.info("="*70)


# --- Utilities for debugging -------------------------------------------------

def inspect_entity_schema(parquet_file: Path):
    """Utility to inspect the schema of Entity parquet files."""
    df = pl.read_parquet(parquet_file)
    print(f"\nFile: {parquet_file.name}")
    print(f"Rows: {len(df):,}")
    print("\nSchema:")
    for col, dtype in df.schema.items():
        print(f"  {col}: {dtype}")
    
    # Sample first row to understand structure
    if len(df) > 0:
        print("\nFirst row sample:")
        first = df.head(1).to_dicts()[0]
        for key, value in first.items():
            if isinstance(value, list) and value:
                print(f"  {key}: {value[:1]}...")  # Show first item of lists
            else:
                print(f"  {key}: {value}")
    return df