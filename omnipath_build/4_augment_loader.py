from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import duckdb
import pandas as pd

try:  # pragma: no cover - exercised via tests when rdkit is installed
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors

    RDKit_AVAILABLE = True
except ImportError:  # pragma: no cover - environment without rdkit
    RDKit_AVAILABLE = False

logger = logging.getLogger(__name__)

PublicationFetcher = Callable[[str], dict[str, Any] | None]

DEFAULT_SMILES_IDENTIFIER_NAMES = {
    "SMILES",
    "CANONICAL SMILES",
    "CANONICAL_SMILES",
    "PRIMARY SMILES",
}


@dataclass(frozen=True)
class CVReferenceSpec:
    filename: str
    namespace_column: str
    term_column: str
    description: str


class CrossrefPublicationFetcher:
    """Fetch publication metadata from the Crossref REST API."""

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        user_agent: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent or "OmniPathBuild/0.1 (mailto:info@omnipathdb.org)"

    def __call__(self, identifier: str) -> dict[str, Any] | None:
        doi = self._extract_doi(identifier)
        if not doi:
            return None

        url = f"https://api.crossref.org/works/{quote(doi)}"
        request = Request(url, headers={"User-Agent": self.user_agent})

        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - network access intentional
                payload = json.loads(response.read())
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("Crossref lookup failed for %s: %s", identifier, exc)
            return None

        message = payload.get("message") if isinstance(payload, dict) else None
        if not message:
            return None

        title = self._first_or_none(message.get("title"))
        journal = self._first_or_none(message.get("container-title"))
        year = self._extract_year(message)
        citation = self._format_citation(message, title, journal, year)

        return {
            "title": title,
            "journal": journal,
            "published_year": year,
            "citation": citation,
        }

    @staticmethod
    def _extract_doi(identifier: str) -> str | None:
        if not identifier:
            return None
        lowered = identifier.strip().lower()
        lowered = lowered.removeprefix("doi:")
        lowered = lowered.removeprefix("https://doi.org/")
        lowered = lowered.removeprefix("http://doi.org/")
        lowered = lowered.removeprefix("doi.org/")
        lowered = lowered.strip()
        return lowered or None

    @staticmethod
    def _first_or_none(values: Any) -> str | None:
        if isinstance(values, (list, tuple)) and values:
            return values[0]
        if isinstance(values, str):
            return values
        return None

    @staticmethod
    def _extract_year(message: dict[str, Any]) -> int | None:
        date_parts = None
        if isinstance(message.get("published-print"), dict):
            date_parts = message["published-print"].get("date-parts")
        if not date_parts and isinstance(message.get("issued"), dict):
            date_parts = message["issued"].get("date-parts")
        if isinstance(date_parts, list) and date_parts and date_parts[0]:
            year = date_parts[0][0]
            if isinstance(year, int):
                return year
        return None

    @staticmethod
    def _format_citation(
        message: dict[str, Any],
        title: str | None,
        journal: str | None,
        year: int | None,
    ) -> str | None:
        authors = []
        for author in message.get("author", []):
            if not isinstance(author, dict):
                continue
            given = author.get("given")
            family = author.get("family")
            if given and family:
                authors.append(f"{family}, {given}")
            elif family:
                authors.append(str(family))
            elif given:
                authors.append(str(given))
        authors_part = "; ".join(authors) if authors else None

        components = [part for part in (authors_part, f"({year})" if year else None, title, journal) if part]
        return ". ".join(components) + "." if components else None


class AugmentLoader:
    """Augments deduplicated gold inputs with computed or fetched metadata."""

    CV_TERM_SPECS: tuple[CVReferenceSpec, ...] = (
        CVReferenceSpec(
            filename="entity_deduped.parquet",
            namespace_column="entity_type_namespace_name",
            term_column="entity_type_name",
            description="Auto-generated entity type",
        ),
        CVReferenceSpec(
            filename="entity_identifier_deduped.parquet",
            namespace_column="identifier_type_namespace_name",
            term_column="identifier_type_name",
            description="Auto-generated identifier type",
        ),
        CVReferenceSpec(
            filename="reference_deduped.parquet",
            namespace_column="type_namespace_name",
            term_column="type_name",
            description="Auto-generated reference type",
        ),
    )

    COMPOUND_COLUMNS = (
        "entity_deduplication_identifier",
        "entity_deduplication_identifier_type",
        "formula",
        "molecular_weight",
        "exact_mass",
        "tpsa",
        "logp",
        "hbd",
        "hba",
        "rotatable_bonds",
        "aromatic_rings",
        "heavy_atoms",
    )

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        deduped_dir: Path,
        *,
        publication_fetcher: PublicationFetcher | None = None,
        smiles_identifier_names: Iterable[str] | None = None,
        compound_limit: int | None = 1000,
    ) -> None:
        self.conn = conn
        self.deduped_dir = Path(deduped_dir)
        self.publication_fetcher = publication_fetcher or CrossrefPublicationFetcher()
        names = smiles_identifier_names or DEFAULT_SMILES_IDENTIFIER_NAMES
        self.smiles_identifier_names = {name.upper() for name in names}
        self.compound_limit = compound_limit

    # ------------------------------------------------------------------
    # Public orchestration
    # ------------------------------------------------------------------
    def run_all(self) -> None:
        self.ensure_cv_terms()
        self.augment_compound_properties()
        self.enrich_publications()

    # ------------------------------------------------------------------
    # CV term enrichment
    # ------------------------------------------------------------------
    def ensure_cv_terms(self) -> None:
        namespace_path = self.deduped_dir / "cv_namespace_deduped.parquet"
        term_path = self.deduped_dir / "cv_term_deduped.parquet"

        if not namespace_path.exists() or not term_path.exists():
            logger.info("Skipping CV term augmentation – prerequisite tables missing")
            return

        missing_namespaces: set[str] = set()
        missing_terms: dict[tuple[str, str], str] = {}

        for spec in self.CV_TERM_SPECS:
            candidate_path = self.deduped_dir / spec.filename
            if not candidate_path.exists():
                continue
            literal = self._duckdb_path_literal(candidate_path)
            rows = self.conn.execute(
                f"""
                SELECT DISTINCT
                    {spec.namespace_column} AS namespace,
                    {spec.term_column} AS term
                FROM read_parquet('{literal}')
                WHERE {spec.namespace_column} IS NOT NULL
                  AND {spec.term_column} IS NOT NULL
                """
            ).fetchall()
            for namespace, term in rows:
                if namespace is None or term is None:
                    continue
                namespace_norm = namespace.strip()
                term_norm = term.strip()
                if not namespace_norm or not term_norm:
                    continue
                missing_namespaces.add(namespace_norm)
                missing_terms.setdefault((namespace_norm, term_norm), spec.description)

        if not missing_namespaces and not missing_terms:
            logger.info("✓ CV term augmentation not required")
            return

        namespace_literal = self._duckdb_path_literal(namespace_path)
        term_literal = self._duckdb_path_literal(term_path)

        existing_namespaces = {
            row[0].strip()
            for row in self.conn.execute(
                f"SELECT DISTINCT name FROM read_parquet('{namespace_literal}')"
            ).fetchall()
            if row[0] is not None and row[0].strip()
        }

        new_namespaces = sorted(missing_namespaces - existing_namespaces)

        existing_terms = {
            (row[0].strip(), row[1].strip())
            for row in self.conn.execute(
                f"SELECT namespace_name, name FROM read_parquet('{term_literal}')"
            ).fetchall()
            if row[0] is not None and row[1] is not None and row[0].strip() and row[1].strip()
        }

        new_terms = [
            (ns, name, missing_terms[(ns, name)])
            for ns, name in missing_terms.keys()
            if (ns, name) not in existing_terms
        ]

        if new_namespaces:
            max_id = self._scalar_query(
                f"SELECT COALESCE(MAX(id), 0) FROM read_parquet('{namespace_literal}')"
            )
            values_sql = ', '.join(
                f"({max_id + idx + 1}, {self._sql_quote(ns)})"
                for idx, ns in enumerate(new_namespaces)
            )
            self.conn.execute(
                f"""
                COPY (
                    SELECT * FROM read_parquet('{namespace_literal}')
                    UNION ALL
                    SELECT * FROM (VALUES {values_sql}) AS v(id, name)
                ) TO '{namespace_literal}' (FORMAT PARQUET)
                """
            )

        if new_terms:
            max_id = self._scalar_query(
                f"SELECT COALESCE(MAX(id), 0) FROM read_parquet('{term_literal}')"
            )
            values_sql = ', '.join(
                f"({max_id + idx + 1}, {self._sql_quote(name)}, NULL, {self._sql_quote(desc)}, FALSE, {self._sql_quote(ns)}, NULL, NULL)"
                for idx, (ns, name, desc) in enumerate(new_terms)
            )
            self.conn.execute(
                f"""
                COPY (
                    SELECT * FROM read_parquet('{term_literal}')
                    UNION ALL
                    SELECT * FROM (VALUES {values_sql}) AS v(id, name, accession, description, is_obsolete, namespace_name, replaces_accession, replaced_by_accession)
                ) TO '{term_literal}' (FORMAT PARQUET)
                """
            )

        if new_namespaces or new_terms:
            logger.info(
                "✓ CV term augmentation added %d namespaces and %d terms",
                len(new_namespaces),
                len(new_terms),
            )

    # ------------------------------------------------------------------
    # Compound feature augmentation
    # ------------------------------------------------------------------
    def augment_compound_properties(self) -> None:
        if not RDKit_AVAILABLE:
            logger.info("Skipping compound augmentation – RDKit not available")
            return

        source_path = self.deduped_dir / "entity_identifier_deduped.parquet"
        if not source_path.exists():
            logger.info("Skipping compound augmentation – entity identifier table missing")
            return

        literal = self._duckdb_path_literal(source_path)
        rows = self.conn.execute(
            f"""
            SELECT
                entity_deduplication_identifier,
                entity_deduplication_identifier_type,
                identifier_type_name,
                identifier
            FROM read_parquet('{literal}')
            WHERE identifier IS NOT NULL
            """
        ).fetchall()

        smile_map: dict[tuple[str, str], str] = {}
        for dedup_id, dedup_type, identifier_type_name, identifier in rows:
            if (
                identifier_type_name is None
                or identifier is None
                or dedup_id is None
                or dedup_type is None
            ):
                continue
            if identifier_type_name.upper() not in self.smiles_identifier_names:
                continue
            key = (dedup_id, dedup_type)
            smile_map.setdefault(key, identifier)

        if not smile_map:
            logger.info("Skipping compound augmentation – no SMILES identifiers found")
            return

        items = sorted(smile_map.items())
        if self.compound_limit is not None and len(items) > self.compound_limit:
            logger.info(
                "Limiting compound augmentation to first %d SMILES (out of %d)",
                self.compound_limit,
                len(items),
            )
            items = items[: self.compound_limit]

        new_records = {}
        failed = []

        for key, smiles in items:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                failed.append(key)
                continue

            formula = rdMolDescriptors.CalcMolFormula(mol)
            record = {
                "entity_deduplication_identifier": key[0],
                "entity_deduplication_identifier_type": key[1],
                "formula": formula,
                "molecular_weight": float(Descriptors.MolWt(mol)),
                "exact_mass": float(Descriptors.ExactMolWt(mol)),
                "tpsa": float(rdMolDescriptors.CalcTPSA(mol)),
                "logp": float(Descriptors.MolLogP(mol)),
                "hbd": int(Lipinski.NumHDonors(mol)),
                "hba": int(Lipinski.NumHAcceptors(mol)),
                "rotatable_bonds": int(Lipinski.NumRotatableBonds(mol)),
                "aromatic_rings": int(rdMolDescriptors.CalcNumAromaticRings(mol)),
                "heavy_atoms": int(mol.GetNumHeavyAtoms()),
            }
            new_records[key] = record

        if failed:
            logger.info(
                "Compound augmentation skipped %d identifiers with invalid SMILES",
                len(failed),
            )

        if not new_records:
            logger.info("Skipping compound augmentation – no valid SMILES available")
            return

        compound_path = self.deduped_dir / "compound_deduped.parquet"
        existing_df = self._read_parquet(compound_path) if compound_path.exists() else pd.DataFrame()

        existing_map = {
            (row["entity_deduplication_identifier"], row["entity_deduplication_identifier_type"]): row
            for row in existing_df.to_dict("records")
        }

        existing_map.update(new_records)

        merged_records = list(existing_map.values())
        merged_records.sort(key=lambda rec: (rec["entity_deduplication_identifier"], rec["entity_deduplication_identifier_type"]))
        for idx, record in enumerate(merged_records, start=1):
            record["id"] = idx

        merged_df = pd.DataFrame(merged_records, columns=("id",) + self.COMPOUND_COLUMNS)
        self._write_dataframe(compound_path, merged_df)

        logger.info(
            "✓ Compound augmentation refreshed %d records (computed %d SMILES)",
            len(merged_records),
            len(items) - len(failed),
        )

    # ------------------------------------------------------------------
    # Publication enrichment
    # ------------------------------------------------------------------
    def enrich_publications(self) -> None:
        if self.publication_fetcher is None:
            logger.info("Skipping publication augmentation – no fetcher configured")
            return

        reference_path = self.deduped_dir / "reference_deduped.parquet"
        if not reference_path.exists():
            logger.info("Skipping publication augmentation – reference table missing")
            return

        df = self._read_parquet(reference_path)
        if df.empty or "identifier" not in df.columns:
            logger.info("Skipping publication augmentation – reference table empty")
            return

        needs_update = df[
            df[["title", "journal", "citation", "published_year"]].isna().any(axis=1)
        ]
        if needs_update.empty:
            logger.info("✓ Publication augmentation not required")
            return

        updated_rows = 0
        for row in needs_update.to_dict("records"):
            identifier = row.get("identifier")
            if not identifier:
                continue
            metadata = self.publication_fetcher(identifier)
            if not metadata:
                continue
            row_selector = df["id"] == row.get("id")
            if metadata.get("title"):
                df.loc[row_selector, "title"] = metadata["title"]
            if metadata.get("journal"):
                df.loc[row_selector, "journal"] = metadata["journal"]
            if metadata.get("citation"):
                df.loc[row_selector, "citation"] = metadata["citation"]
            if metadata.get("published_year"):
                df.loc[row_selector, "published_year"] = metadata["published_year"]
            updated_rows += 1

        if updated_rows:
            self._write_dataframe(reference_path, df)
            logger.info("✓ Publication augmentation updated %d references", updated_rows)
        else:
            logger.info("Publication augmentation found no metadata to apply")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _read_parquet(self, path: Path) -> pd.DataFrame:
        literal = self._duckdb_path_literal(path)
        return self.conn.execute(f"SELECT * FROM read_parquet('{literal}')").df()

    def _write_dataframe(self, path: Path, dataframe: pd.DataFrame) -> None:
        literal = self._duckdb_path_literal(path)
        view_name = f"_aug_{uuid.uuid4().hex}"
        self.conn.register(view_name, dataframe)
        try:
            self.conn.execute(
                f"COPY {view_name} TO '{literal}' (FORMAT PARQUET)"
            )
        finally:
            self.conn.unregister(view_name)

    def _scalar_query(self, sql: str) -> int:
        result = self.conn.execute(sql).fetchone()
        return int(result[0]) if result and result[0] is not None else 0

    @staticmethod
    def _duckdb_path_literal(path: Path | str) -> str:
        return str(path).replace("'", "''")

    @staticmethod
    def _sql_quote(value: str | None) -> str:
        if value is None:
            return "NULL"
        escaped = value.replace("'", "''")
        return f"'{escaped}'"


__all__ = [
    'CVReferenceSpec',
    'CrossrefPublicationFetcher',
    'DEFAULT_SMILES_IDENTIFIER_NAMES',
    'AugmentLoader',
    'PublicationFetcher',
    'RDKit_AVAILABLE',
]
