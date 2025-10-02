"""
Idempotent Data Ingestion System for Biological/Chemical Database
Supports incremental loading with deduplication and provenance tracking
"""

import duckdb
from typing import Optional, Dict, List, Any, Callable
from datetime import datetime
from dataclasses import dataclass
import hashlib

__all__ = [
    'IngestionManager',
    'SourceConfig',
    'clean_sequence',
    'normalize_inchikey',
    'normalize_uniprot',
]


@dataclass
class SourceConfig:
    """Configuration for a data source"""
    name: str
    url: Optional[str] = None
    description: Optional[str] = None
    is_primary: bool = False


class IngestionManager:
    """Manages idempotent data ingestion operations"""
    
    def __init__(self, db_path: str):
        self.conn = duckdb.connect(db_path)
        self._preprocessors: Dict[str, Callable] = {}
        
    def register_preprocessor(self, field_name: str, func: Callable):
        """Register a preprocessing function for a specific field"""
        self._preprocessors[field_name] = func
    
    def preprocess(self, field_name: str, value: Any) -> Any:
        """Apply preprocessing if registered"""
        if field_name in self._preprocessors and value is not None:
            return self._preprocessors[field_name](value)
        return value
    
    # ==================== CV TERM OPERATIONS ====================
    
    def get_or_create_cv_namespace(self, name: str, uri: Optional[str] = None, 
                                   description: Optional[str] = None) -> int:
        """Get or create a CV namespace"""
        result = self.conn.execute("""
            SELECT id FROM cv_namespace WHERE name = ?
        """, [name]).fetchone()
        
        if result:
            return result[0]
        
        self.conn.execute("""
            INSERT INTO cv_namespace (name, uri, description)
            VALUES (?, ?, ?)
        """, [name, uri, description])
        
        return self.conn.execute("SELECT lastval()").fetchone()[0]
    
    def get_or_create_cv_term(self, namespace: str, accession: str, 
                              name: str, description: Optional[str] = None) -> int:
        """Get or create a CV term"""
        # Ensure namespace exists
        namespace_id = self.get_or_create_cv_namespace(namespace)
        
        # Check if term exists
        result = self.conn.execute("""
            SELECT id FROM cv_term 
            WHERE namespace_id = ? AND accession = ?
        """, [namespace_id, accession]).fetchone()
        
        if result:
            return result[0]
        
        # Create new term
        self.conn.execute("""
            INSERT INTO cv_term (namespace_id, accession, name, description, is_obsolete)
            VALUES (?, ?, ?, ?, false)
        """, [namespace_id, accession, name, description])
        
        return self.conn.execute("SELECT lastval()").fetchone()[0]
    
    # ==================== SOURCE & PROVENANCE OPERATIONS ====================
    
    def get_or_create_source(self, config: SourceConfig) -> int:
        """Get or create a data source"""
        result = self.conn.execute("""
            SELECT id FROM source WHERE name = ?
        """, [config.name]).fetchone()
        
        if result:
            return result[0]
        
        self.conn.execute("""
            INSERT INTO source (name, url, description, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, [config.name, config.url, config.description])
        
        return self.conn.execute("SELECT lastval()").fetchone()[0]
    
    def get_or_create_reference(self, pmid: Optional[str] = None, 
                               doi: Optional[str] = None,
                               chembl_doc_id: Optional[str] = None,
                               citation: Optional[str] = None,
                               year: Optional[int] = None,
                               journal: Optional[str] = None,
                               title: Optional[str] = None) -> Optional[int]:
        """Get or create a reference by PMID, DOI, or ChEMBL document ID"""
        if not any([pmid, doi, chembl_doc_id]):
            return None
        
        # Determine reference type
        if pmid:
            type_id = self.get_or_create_cv_term('reference_type', 'pmid', 'PubMed ID')
            value = pmid
        elif doi:
            type_id = self.get_or_create_cv_term('reference_type', 'doi', 'DOI')
            value = doi
        else:
            type_id = self.get_or_create_cv_term('reference_type', 'chembl_doc_id', 'ChEMBL Document ID')
            value = chembl_doc_id
        
        # Check if reference exists
        result = self.conn.execute("""
            SELECT id FROM reference 
            WHERE type_id = ? AND value = ?
        """, [type_id, value]).fetchone()
        
        if result:
            return result[0]
        
        # Create new reference
        self.conn.execute("""
            INSERT INTO reference (type_id, value, citation, year, journal, title)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [type_id, value, citation, year, journal, title])
        
        return self.conn.execute("SELECT lastval()").fetchone()[0]
    
    def get_or_create_provenance(self, source_id: int, 
                                primary_source_id: Optional[int] = None,
                                reference_id: Optional[int] = None) -> int:
        """Get or create provenance record"""
        # Check if exact provenance exists
        result = self.conn.execute("""
            SELECT id FROM provenance 
            WHERE source_id = ? 
              AND (primary_source_id = ? OR (primary_source_id IS NULL AND ? IS NULL))
              AND (reference_id = ? OR (reference_id IS NULL AND ? IS NULL))
        """, [source_id, primary_source_id, primary_source_id, 
              reference_id, reference_id]).fetchone()
        
        if result:
            return result[0]
        
        # Create new provenance
        self.conn.execute("""
            INSERT INTO provenance (source_id, primary_source_id, reference_id, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, [source_id, primary_source_id, reference_id])
        
        return self.conn.execute("SELECT lastval()").fetchone()[0]
    
    # ==================== ANNOTATION OPERATIONS ====================
    
    def create_annotation_record(self, provenance_id: int, 
                                note: Optional[str] = None) -> int:
        """Create a new annotation record"""
        self.conn.execute("""
            INSERT INTO annotation_record (provenance_id, created_at, note)
            VALUES (?, CURRENT_TIMESTAMP, ?)
        """, [provenance_id, note])
        
        return self.conn.execute("SELECT lastval()").fetchone()[0]
    
    def add_annotation(self, record_id: int, term: str, term_namespace: str,
                      value_text: Optional[str] = None,
                      value_num: Optional[float] = None,
                      value_term: Optional[tuple] = None,  # (namespace, accession)
                      units: Optional[str] = None):
        """Add an annotation to a record"""
        term_id = self.get_or_create_cv_term(term_namespace, term, term)
        value_term_id = None
        
        if value_term:
            value_term_id = self.get_or_create_cv_term(
                value_term[0], value_term[1], value_term[1]
            )
        
        self.conn.execute("""
            INSERT INTO annotation (record_id, term_id, value_term_id, 
                                   value_text, value_num, units, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [record_id, term_id, value_term_id, value_text, value_num, units])
    
    # ==================== ENTITY OPERATIONS ====================
    
    def get_or_create_entity(self,
                            dedup_identifier: str,
                            dedup_identifier_type: str,
                            entity_type: str,  # e.g., 'protein', 'compound', 'reaction'
                            source_config: SourceConfig,
                            primary_source_config: Optional[SourceConfig] = None,
                            reference: Optional[Dict[str, Any]] = None,
                            other_identifiers: Optional[str] = None,  # Format: "type1:value1|type2:value2"
                            annotations: Optional[List[Dict[str, Any]]] = None,
                            entity_details: Optional[Dict[str, Any]] = None) -> int:
        """
        Get or create an entity with full provenance and deduplication
        
        Args:
            dedup_identifier: The identifier value for deduplication (e.g., InChIKey, UniProt ID)
            dedup_identifier_type: The type of identifier (e.g., 'inchikey', 'uniprot')
            entity_type: Type of entity ('protein', 'compound', 'reaction')
            source_config: Configuration for the data source
            primary_source_config: Optional primary source if this is derived data
            reference: Optional reference dict with 'pmid', 'doi', or 'chembl_doc_id'
            other_identifiers: Pipe-separated identifiers (e.g., "chebi:123|chembl:CHEMBL123")
            annotations: List of annotation dicts
            entity_details: Dict with entity-specific details (name, sequence, formula, etc.)
        
        Returns:
            Entity ID
        """
        # Preprocess dedup_identifier
        dedup_identifier = self.preprocess(dedup_identifier_type, dedup_identifier)
        
        # Get CV term for identifier type
        identifier_type_id = self.get_or_create_cv_term(
            'identifier_type', dedup_identifier_type, dedup_identifier_type
        )
        
        # Check if entity exists by dedup identifier
        result = self.conn.execute("""
            SELECT e.id 
            FROM entity e
            JOIN entity_identifier ei ON e.id = ei.entity_id
            WHERE ei.cv_term_id = ? AND ei.identifier = ?
        """, [identifier_type_id, dedup_identifier]).fetchone()
        
        if result:
            return result[0]
        
        # Create new entity
        entity_type_id = self.get_or_create_cv_term('entity_type', entity_type, entity_type)
        
        self.conn.execute("""
            INSERT INTO entity (cv_term_id, created_at)
            VALUES (?, CURRENT_TIMESTAMP)
        """, [entity_type_id])
        
        entity_id = self.conn.execute("SELECT lastval()").fetchone()[0]
        
        # Create provenance
        source_id = self.get_or_create_source(source_config)
        primary_source_id = None
        if primary_source_config:
            primary_source_id = self.get_or_create_source(primary_source_config)
        
        reference_id = None
        if reference:
            reference_id = self.get_or_create_reference(**reference)
        
        provenance_id = self.get_or_create_provenance(
            source_id, primary_source_id, reference_id
        )
        
        # Add dedup identifier
        self.conn.execute("""
            INSERT INTO entity_identifier (entity_id, cv_term_id, identifier, 
                                          created_at, provenance_id)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
        """, [entity_id, identifier_type_id, dedup_identifier, provenance_id])
        
        # Add other identifiers
        if other_identifiers:
            for identifier_str in other_identifiers.split('|'):
                if ':' in identifier_str:
                    id_type, id_value = identifier_str.split(':', 1)
                    id_type = id_type.strip()
                    id_value = self.preprocess(id_type, id_value.strip())
                    
                    id_type_id = self.get_or_create_cv_term(
                        'identifier_type', id_type, id_type
                    )
                    
                    self.conn.execute("""
                        INSERT INTO entity_identifier (entity_id, cv_term_id, identifier,
                                                      created_at, provenance_id)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
                    """, [entity_id, id_type_id, id_value, provenance_id])
        
        # Add annotations if provided
        if annotations:
            annotation_record_id = self.create_annotation_record(provenance_id)
            
            for ann in annotations:
                self.add_annotation(annotation_record_id, **ann)
            
            # Link annotation record to entity
            self.conn.execute("""
                INSERT INTO entity_annotation_record (entity_id, annotation_record_id, role)
                VALUES (?, ?, 'primary')
            """, [entity_id, annotation_record_id])
        
        # Add entity-specific details
        if entity_details:
            if entity_type == 'protein':
                self._create_protein_details(entity_id, entity_details)
            elif entity_type == 'compound':
                self._create_compound_details(entity_id, entity_details)
            elif entity_type == 'reaction':
                self._create_reaction_details(entity_id, entity_details)
        
        return entity_id
    
    def _create_protein_details(self, entity_id: int, details: Dict[str, Any]):
        """Create protein-specific details"""
        self.conn.execute("""
            INSERT INTO protein (entity_id, name, class, sequence)
            VALUES (?, ?, ?, ?)
        """, [entity_id, 
              details.get('name'),
              details.get('class'),
              details.get('sequence')])
    
    def _create_compound_details(self, entity_id: int, details: Dict[str, Any]):
        """Create compound-specific details"""
        self.conn.execute("""
            INSERT INTO compound (entity_id, formula, molecular_weight, exact_mass,
                                 tpsa, logp, hbd, hba, rotatable_bonds, 
                                 aromatic_rings, heavy_atoms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [entity_id,
              details.get('formula'),
              details.get('molecular_weight'),
              details.get('exact_mass'),
              details.get('tpsa'),
              details.get('logp'),
              details.get('hbd'),
              details.get('hba'),
              details.get('rotatable_bonds'),
              details.get('aromatic_rings'),
              details.get('heavy_atoms')])
    
    def _create_reaction_details(self, entity_id: int, details: Dict[str, Any]):
        """Create reaction-specific details"""
        self.conn.execute("""
            INSERT INTO reaction (entity_id, equation, directionality, pathway,
                                ec_number, smiles)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [entity_id,
              details.get('equation'),
              details.get('directionality'),
              details.get('pathway'),
              details.get('ec_number'),
              details.get('smiles')])
    
    # ==================== INTERACTION OPERATIONS ====================
    
    def create_interaction(self, entity_a_id: int, entity_b_id: int) -> int:
        """Create or get interaction between two entities"""
        # Check if interaction exists (bidirectional)
        result = self.conn.execute("""
            SELECT id FROM interaction 
            WHERE (entity_a_id = ? AND entity_b_id = ?)
               OR (entity_a_id = ? AND entity_b_id = ?)
        """, [entity_a_id, entity_b_id, entity_b_id, entity_a_id]).fetchone()
        
        if result:
            return result[0]
        
        self.conn.execute("""
            INSERT INTO interaction (entity_a_id, entity_b_id)
            VALUES (?, ?)
        """, [entity_a_id, entity_b_id])
        
        return self.conn.execute("SELECT lastval()").fetchone()[0]
    
    def add_interaction_evidence(self,
                                interaction_id: int,
                                source_config: SourceConfig,
                                evidence_type: str,
                                reference: Optional[Dict[str, Any]] = None,
                                is_directed: bool = False,
                                direction: Optional[str] = None,
                                sign: Optional[str] = None,
                                sentence: Optional[str] = None,
                                annotations: Optional[List[Dict[str, Any]]] = None,
                                entity_a_annotations: Optional[List[Dict[str, Any]]] = None,
                                entity_b_annotations: Optional[List[Dict[str, Any]]] = None) -> int:
        """Add evidence for an interaction"""
        # Create provenance
        source_id = self.get_or_create_source(source_config)
        reference_id = None
        if reference:
            reference_id = self.get_or_create_reference(**reference)
        
        provenance_id = self.get_or_create_provenance(source_id, None, reference_id)
        
        # Get type ID
        type_id = self.get_or_create_cv_term('interaction_type', evidence_type, evidence_type)
        
        # Get optional CV term IDs
        direction_id = None
        if direction:
            direction_id = self.get_or_create_cv_term('direction', direction, direction)
        
        sign_id = None
        if sign:
            sign_id = self.get_or_create_cv_term('sign', sign, sign)
        
        # Create annotation records
        annotation_record_id = None
        if annotations:
            annotation_record_id = self.create_annotation_record(provenance_id)
            for ann in annotations:
                self.add_annotation(annotation_record_id, **ann)
        
        entity_a_ann_id = None
        if entity_a_annotations:
            entity_a_ann_id = self.create_annotation_record(provenance_id)
            for ann in entity_a_annotations:
                self.add_annotation(entity_a_ann_id, **ann)
        
        entity_b_ann_id = None
        if entity_b_annotations:
            entity_b_ann_id = self.create_annotation_record(provenance_id)
            for ann in entity_b_annotations:
                self.add_annotation(entity_b_ann_id, **ann)
        
        # Insert evidence
        self.conn.execute("""
            INSERT INTO interaction_evidence (
                interaction_id, provenance_id, annotation_record_id,
                entity_a_annotation_record_id, entity_b_annotation_record_id,
                type_id, direction_id, sign_id, sentence, is_directed, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [interaction_id, provenance_id, annotation_record_id,
              entity_a_ann_id, entity_b_ann_id, type_id, direction_id,
              sign_id, sentence, is_directed])
        
        return self.conn.execute("SELECT lastval()").fetchone()[0]
    
    # ==================== BULK OPERATIONS ====================
    
    def bulk_load_entities(self, data_source: str, entity_type: str,
                          source_config: SourceConfig,
                          preprocessors: Optional[Dict[str, Callable]] = None):
        """
        Load entities in bulk from a table/view in DuckDB
        
        Args:
            data_source: Name of source table/view containing entity data
            entity_type: Type of entities being loaded
            source_config: Source configuration
            preprocessors: Optional dict of field-specific preprocessing functions
        """
        if preprocessors:
            for field, func in preprocessors.items():
                self.register_preprocessor(field, func)
        
        # This is a template - actual implementation depends on source schema
        query = f"""
        SELECT * FROM {data_source}
        WHERE NOT EXISTS (
            SELECT 1 FROM entity e
            JOIN entity_identifier ei ON e.id = ei.entity_id
            WHERE ei.identifier = {data_source}.dedup_identifier
        )
        """
        
        # Process in batches
        for row in self.conn.execute(query).fetchall():
            # Call get_or_create_entity for each row
            pass
    
    def commit(self):
        """Commit the current transaction"""
        self.conn.commit()
    
    def close(self):
        """Close the database connection"""
        self.conn.close()


# ==================== EXAMPLE PREPROCESSING FUNCTIONS ====================

def normalize_inchikey(inchikey: str) -> str:
    """Normalize InChIKey format"""
    return inchikey.strip().upper()

def normalize_uniprot(uniprot_id: str) -> str:
    """Normalize UniProt ID"""
    return uniprot_id.strip().upper()

def clean_sequence(sequence: str) -> str:
    """Clean protein sequence"""
    return ''.join(sequence.split()).upper()


# ==================== EXAMPLE USAGE ====================

if __name__ == "__main__":
    # Initialize manager
    manager = IngestionManager("my_database.db")
    
    # Register preprocessors
    manager.register_preprocessor('inchikey', normalize_inchikey)
    manager.register_preprocessor('uniprot', normalize_uniprot)
    manager.register_preprocessor('sequence', clean_sequence)
    
    # Example: Load a compound from ChEMBL
    chembl_source = SourceConfig(
        name="ChEMBL",
        url="https://www.ebi.ac.uk/chembl/",
        description="ChEMBL Database"
    )
    
    entity_id = manager.get_or_create_entity(
        dedup_identifier="BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
        dedup_identifier_type="inchikey",
        entity_type="compound",
        source_config=chembl_source,
        other_identifiers="chembl:CHEMBL25|chebi:CHEBI:15377",
        entity_details={
            'formula': 'H2O',
            'molecular_weight': 18.015,
            'exact_mass': 18.0106,
        },
        annotations=[
            {
                'term': 'common_name',
                'term_namespace': 'compound_property',
                'value_text': 'Water'
            }
        ]
    )
    
    # Example: Load a protein from UniProt
    uniprot_source = SourceConfig(
        name="UniProt",
        url="https://www.uniprot.org/",
        description="Universal Protein Resource"
    )
    
    protein_id = manager.get_or_create_entity(
        dedup_identifier="P12345",
        dedup_identifier_type="uniprot",
        entity_type="protein",
        source_config=uniprot_source,
        other_identifiers="ensembl:ENSP00000123456",
        entity_details={
            'name': 'Example Protein',
            'class': 'Enzyme',
            'sequence': 'MKTIIALSYIFCLVFA'
        },
        reference={'pmid': '12345678'}
    )
    
    # Example: Create an interaction
    interaction_id = manager.create_interaction(protein_id, entity_id)
    
    # Add evidence for the interaction
    string_source = SourceConfig(
        name="STRING",
        url="https://string-db.org/",
        description="STRING Database"
    )
    
    manager.add_interaction_evidence(
        interaction_id=interaction_id,
        source_config=string_source,
        evidence_type="binding",
        is_directed=False,
        reference={'pmid': '98765432'},
        annotations=[
            {
                'term': 'confidence_score',
                'term_namespace': 'interaction_quality',
                'value_num': 0.95
            }
        ]
    )
    
    manager.commit()
    manager.close()
