from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pypath.internals.cv_terms import (
    BiologicalEffectCv,
    BiologicalRoleCv,
    CausalStatementCv,
    ExperimentalRoleCv,
    IdentifierNamespaceCv,
    InteractionMetadataCv,
    ParticipantMetadataCv,
)
from pypath.internals.cv_terms.entity_types import EntityTypeCv

from omnipath_build.gold.canonicalize import normalize_target_schema_dir, write_canonicalization_overview_report
from omnipath_build.gold.cv_terms import format_cv_term
from omnipath_build.gold.dedup import deduplicate_target_schema_dir
from omnipath_build.silver.ensure import ensure_silver_dir
from omnipath_build.silver.paths import default_silver_dir

ATTRIBUTES_STRUCT = pa.list_(
    pa.struct([
        pa.field("term", pa.string()),
        pa.field("value", pa.string()),
        pa.field("unit", pa.string()),
    ])
)

ENTITY_IDENTIFIERS_SCHEMA = pa.schema([
    pa.field("entity_id", pa.int64()),
    pa.field("identifier", pa.string()),
    pa.field("identifier_type", pa.string()),
    pa.field("is_canonical", pa.bool_()),
    pa.field("source", pa.string()),
])

ENTITY_IDENTIFIERS_SOURCE_SCHEMA = pa.schema([
    pa.field("entity_id", pa.int64()),
    pa.field("identifier", pa.string()),
    pa.field("identifier_type", pa.string()),
    pa.field("source", pa.string()),
])

ENTITIES_SCHEMA = pa.schema([
    pa.field("entity_id", pa.int64()),
    pa.field("entity_type", pa.string()),
    pa.field("entity_attributes", ATTRIBUTES_STRUCT),
    pa.field("taxonomy_id", pa.string()),
    pa.field("source", pa.string()),
])

INTERACTIONS_SCHEMA = pa.schema([
    pa.field("interaction_id", pa.int64()),
    pa.field("entity_a_id", pa.int64()),
    pa.field("entity_b_id", pa.int64()),
    pa.field("direction", pa.int64()),
    pa.field("sign", pa.int64()),
    pa.field("record_attributes", ATTRIBUTES_STRUCT),
    pa.field("entity_a_attributes", ATTRIBUTES_STRUCT),
    pa.field("entity_b_attributes", ATTRIBUTES_STRUCT),
    pa.field("evidence", ATTRIBUTES_STRUCT),
    pa.field("source", pa.string()),
])

ASSOCIATIONS_SCHEMA = pa.schema([
    pa.field("association_id", pa.int64()),
    pa.field("parent_entity_id", pa.int64()),
    pa.field("member_entity_id", pa.int64()),
    pa.field("role_term_id", pa.string()),
    pa.field("stoichiometry", pa.string()),
    pa.field("record_attributes", ATTRIBUTES_STRUCT),
    pa.field("parent_attributes", ATTRIBUTES_STRUCT),
    pa.field("member_attributes", ATTRIBUTES_STRUCT),
    pa.field("evidence", ATTRIBUTES_STRUCT),
    pa.field("source", pa.string()),
])

ANNOTATIONS_SCHEMA = pa.schema([
    pa.field("subject_type", pa.string()),
    pa.field("subject_id", pa.int64()),
    pa.field("cv_term", pa.string()),
    pa.field("source", pa.string()),
])

ACCESSION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:[^\s]+$")
WIKIPATHWAYS_RE = re.compile(r"^WP\d+(?:_r\d+)?$")
REACTOME_RE = re.compile(r"^R-[A-Z]{3}-\d+(?:-\d+)?$")
INTERACTION_LIKE_TYPES = {
    str(EntityTypeCv.INTERACTION),
    str(EntityTypeCv.REACTION),
    str(EntityTypeCv.CATALYSIS),
    str(EntityTypeCv.CONTROL),
    str(EntityTypeCv.DEGRADATION),
}
PURE_INTERACTION_TYPES = {
    str(EntityTypeCv.INTERACTION),
}
SIGN_POSITIVE_TERMS = {
    str(BiologicalEffectCv.UP_REGULATES_ACTIVITY),
    str(BiologicalEffectCv.UP_REGULATES_QUANTITY),
    str(CausalStatementCv.UP_REGULATES),
    str(CausalStatementCv.UP_REGULATES_ACTIVITY),
    str(CausalStatementCv.UP_REGULATES_QUANTITY),
    str(CausalStatementCv.UP_REGULATES_QUANTITY_BY_EXPRESSION),
    str(CausalStatementCv.UP_REGULATES_QUANTITY_BY_STABILIZATION),
}
SIGN_NEGATIVE_TERMS = {
    str(BiologicalEffectCv.DOWN_REGULATES_ACTIVITY),
    str(BiologicalEffectCv.DOWN_REGULATES_QUANTITY),
    str(CausalStatementCv.DOWN_REGULATES),
    str(CausalStatementCv.DOWN_REGULATES_ACTIVITY),
    str(CausalStatementCv.DOWN_REGULATES_QUANTITY),
    str(CausalStatementCv.DOWN_REGULATES_QUANTITY_BY_DESTABLIZATION),
    str(CausalStatementCv.DOWN_REGULATES_QUANTITY_BY_REPRESSION),
}
ROLE_TERMS = (
    {str(term) for term in BiologicalRoleCv}
    | {str(term) for term in ExperimentalRoleCv}
    | {str(ParticipantMetadataCv.SOURCE), str(ParticipantMetadataCv.TARGET)}
)
EVIDENCE_TERMS = {
    str(IdentifierNamespaceCv.PUBMED),
    str(IdentifierNamespaceCv.PUBMED_CENTRAL),
    str(IdentifierNamespaceCv.DOI),
    str(IdentifierNamespaceCv.PATENT_NUMBER),
}


@dataclass
class EntityRef:
    entity_id: int
    entity_type_id: str | None
    entity_attributes: list[dict[str, str | None]] | None


class BufferedParquetWriter:
    def __init__(self, path: Path, schema: pa.Schema, batch_size: int = 10_000) -> None:
        self.path = path
        self.schema = schema
        self.batch_size = batch_size
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.writer: pq.ParquetWriter | None = None
        self.rows: list[dict[str, Any]] = []
        self.row_count = 0

    def write(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        table = pa.Table.from_pylist(self.rows, schema=self.schema)
        if self.writer is None:
            self.writer = pq.ParquetWriter(self.path, self.schema)
        self.writer.write_table(table)
        self.row_count += len(self.rows)
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is None:
            if self.path.exists():
                self.path.unlink()
            return
        self.writer.close()


class SourceConverter:
    def __init__(self, source: str, silver_dir: Path, output_dir: Path, batch_size: int = 10_000) -> None:
        self.source = source
        self.silver_dir = silver_dir
        self.output_dir = output_dir
        self.batch_size = batch_size

        self.next_entity_id = 1
        self.next_interaction_id = 1
        self.next_association_id = 1

        self.entities = BufferedParquetWriter(output_dir / "entities.parquet", ENTITIES_SCHEMA, batch_size)
        self.entity_identifiers_resolved = BufferedParquetWriter(output_dir / "entity_identifiers_resolved.parquet", ENTITY_IDENTIFIERS_SCHEMA, batch_size)
        self.entity_identifiers_source = BufferedParquetWriter(output_dir / "entity_identifiers_source.parquet", ENTITY_IDENTIFIERS_SOURCE_SCHEMA, batch_size)
        self.interactions = BufferedParquetWriter(output_dir / "interactions.parquet", INTERACTIONS_SCHEMA, batch_size)
        self.associations = BufferedParquetWriter(output_dir / "associations.parquet", ASSOCIATIONS_SCHEMA, batch_size)
        self.annotations = BufferedParquetWriter(output_dir / "annotations.parquet", ANNOTATIONS_SCHEMA, batch_size)

    def close(self) -> None:
        self.entities.close()
        self.entity_identifiers_resolved.close()
        self.entity_identifiers_source.close()
        self.interactions.close()
        self.associations.close()
        self.annotations.close()

    def convert(self) -> None:
        parquet_files = sorted(
            path for path in self.silver_dir.glob("*.parquet") if path.name != "resource.parquet"
        )
        print(f"[{self.source}] converting {len(parquet_files)} silver parquet(s) from {self.silver_dir}")

        for parquet_path in parquet_files:
            print(f"[{self.source}] reading {parquet_path.name}")
            pf = pq.ParquetFile(parquet_path)
            for batch in pf.iter_batches(batch_size=self.batch_size):
                for row in batch.to_pylist():
                    self._process_entity_row(row)

    def _process_entity_row(self, row: dict[str, Any]) -> EntityRef | None:
        parent_type = self._string_or_none(row.get("type"))
        memberships = row.get("membership") or []

        if parent_type in PURE_INTERACTION_TYPES:
            if not memberships:
                return None

            members: list[dict[str, Any]] = []
            for membership in memberships:
                member_row = membership.get("member") or {}
                member_ref = self._materialize_member_entity(member_row)
                if member_ref is None:
                    continue
                membership_annotations = membership.get("annotations") or []
                merged_member_attrs = self._annotations_to_attributes(membership_annotations)
                members.append({
                    "ref": member_ref,
                    "is_parent": bool(membership.get("is_parent", False)),
                    "membership_annotations": membership_annotations,
                    "member_attributes": merged_member_attrs,
                    "role_term_id": self._derive_role_term_id(membership_annotations),
                    "stoichiometry": self._derive_stoichiometry(membership_annotations),
                })

            parent_annotations = row.get("annotations") or []
            evidence = self._annotations_to_attributes(parent_annotations, evidence_only=True)
            non_evidence = self._annotations_to_attributes(parent_annotations, evidence_only=False)

            if len(members) == 2:
                interaction_id = self.next_interaction_id
                self.next_interaction_id += 1

                entity_a, entity_b = self._order_interaction_members(members)
                self.interactions.write({
                    "interaction_id": interaction_id,
                    "entity_a_id": entity_a["ref"].entity_id,
                    "entity_b_id": entity_b["ref"].entity_id,
                    "direction": self._derive_direction(parent_annotations, entity_a["membership_annotations"], entity_b["membership_annotations"]),
                    "sign": self._derive_sign(parent_annotations),
                    "record_attributes": non_evidence,
                    "entity_a_attributes": entity_a["member_attributes"],
                    "entity_b_attributes": entity_b["member_attributes"],
                    "evidence": evidence,
                    "source": self.source,
                })
                self._emit_cv_annotations("interaction", interaction_id, parent_annotations)
            return None

        parent = self._materialize_entity(row)
        if not memberships:
            self._emit_cv_annotations("entity", parent.entity_id, row.get("annotations") or [])
            return parent

        members: list[dict[str, Any]] = []
        for membership in memberships:
            member_row = membership.get("member") or {}
            member_ref = self._materialize_member_entity(member_row)
            if member_ref is None:
                continue
            membership_annotations = membership.get("annotations") or []
            merged_member_attrs = self._annotations_to_attributes(membership_annotations)
            members.append({
                "ref": member_ref,
                "is_parent": bool(membership.get("is_parent", False)),
                "membership_annotations": membership_annotations,
                "member_attributes": merged_member_attrs,
                "role_term_id": self._derive_role_term_id(membership_annotations),
                "stoichiometry": self._derive_stoichiometry(membership_annotations),
            })

        parent_annotations = row.get("annotations") or []
        evidence = self._annotations_to_attributes(parent_annotations, evidence_only=True)

        for member in members:
            parent_entity_id = parent.entity_id
            member_entity_id = member["ref"].entity_id
            if member["is_parent"]:
                parent_entity_id, member_entity_id = member_entity_id, parent_entity_id

            association_id = self.next_association_id
            self.next_association_id += 1
            self.associations.write({
                "association_id": association_id,
                "parent_entity_id": parent_entity_id,
                "member_entity_id": member_entity_id,
                "role_term_id": member["role_term_id"],
                "stoichiometry": member["stoichiometry"],
                "record_attributes": None,
                "parent_attributes": None,
                "member_attributes": member["member_attributes"],
                "evidence": evidence,
                "source": self.source,
            })
        self._emit_cv_annotations("entity", parent.entity_id, parent_annotations)

        return parent

    def _materialize_member_entity(self, row: dict[str, Any]) -> EntityRef | None:
        if self._string_or_none(row.get("type")) in PURE_INTERACTION_TYPES:
            self._process_entity_row(row)
            return None
        entity = self._materialize_entity(row)
        self._emit_cv_annotations("entity", entity.entity_id, row.get("annotations") or [])
        return entity

    def _materialize_entity(self, row: dict[str, Any]) -> EntityRef:
        entity_id = self.next_entity_id
        self.next_entity_id += 1

        identifiers = [ident for ident in (row.get("identifiers") or []) if ident and ident.get("value")]
        taxonomy_id = self._extract_taxonomy_id(
            row.get("annotations") or [],
            identifiers,
            row.get("membership") or [],
        )
        entity_attributes = self._annotations_to_attributes(row.get("annotations") or [], evidence_only=False)

        self.entities.write({
            "entity_id": entity_id,
            "entity_type": format_cv_term(self._string_or_none(row.get("type"))),
            "entity_attributes": entity_attributes,
            "taxonomy_id": taxonomy_id,
            "source": self.source,
        })

        for ident in identifiers:
            ident_type = self._string_or_none(ident.get("type"))
            ident_value = self._string_or_none(ident.get("value"))
            self.entity_identifiers_source.write({
                "entity_id": entity_id,
                "identifier": ident_value,
                "identifier_type": format_cv_term(ident_type),
                "source": self.source,
            })

        return EntityRef(
            entity_id=entity_id,
            entity_type_id=self._string_or_none(row.get("type")),
            entity_attributes=entity_attributes,
        )

    def _emit_cv_annotations(self, subject_type: str, subject_id: int, annotations: list[dict[str, Any]]) -> None:
        seen: set[str] = set()
        for annotation in annotations:
            cv_term = self._cv_term_from_annotation(annotation)
            if not cv_term or cv_term in seen:
                continue
            seen.add(cv_term)
            self.annotations.write({
                "subject_type": subject_type,
                "subject_id": subject_id,
                "cv_term": cv_term,
                "source": self.source,
            })

    def _extract_taxonomy_id(
        self,
        annotations: list[dict[str, Any]],
        identifiers: list[dict[str, Any]],
        memberships: list[dict[str, Any]],
    ) -> str | None:
        tax_type = str(IdentifierNamespaceCv.NCBI_TAX_ID)
        for ident in identifiers:
            if self._string_or_none(ident.get("type")) == tax_type and ident.get("value") is not None:
                return self._string_or_none(ident.get("value"))
        for annotation in annotations:
            if self._string_or_none(annotation.get("term")) == tax_type and annotation.get("value") is not None:
                return self._string_or_none(annotation.get("value"))

        member_tax_ids: set[str] = set()
        for membership in memberships:
            member = membership.get("member") or {}
            for annotation in member.get("annotations") or []:
                if self._string_or_none(annotation.get("term")) == tax_type and annotation.get("value") is not None:
                    value = self._string_or_none(annotation.get("value"))
                    if value is not None:
                        member_tax_ids.add(value)
        if len(member_tax_ids) == 1:
            return next(iter(member_tax_ids))
        return None

    def _annotations_to_attributes(
        self,
        annotations: list[dict[str, Any]],
        *,
        evidence_only: bool = False,
    ) -> list[dict[str, str | None]] | None:
        rows: list[dict[str, str | None]] = []
        for annotation in annotations:
            term = self._string_or_none(annotation.get("term"))
            value = self._string_or_none(annotation.get("value"))
            unit = self._string_or_none(annotation.get("units"))
            if term is None:
                continue

            is_evidence = term in EVIDENCE_TERMS
            if evidence_only != is_evidence:
                continue

            if not evidence_only:
                if term in {
                    str(IdentifierNamespaceCv.NCBI_TAX_ID),
                    str(IdentifierNamespaceCv.CV_TERM_ACCESSION),
                }:
                    continue
                if value is None:
                    continue

            rows.append({
                "term": self._normalize_attribute_term(term),
                "value": value,
                "unit": self._normalize_attribute_term(unit) if unit is not None else None,
            })
        return rows or None

    def _derive_role_term_id(self, annotations: list[dict[str, Any]]) -> str | None:
        for annotation in annotations:
            term = self._string_or_none(annotation.get("term"))
            if term in ROLE_TERMS:
                return self._format_cv_term(term)
        return None

    def _derive_stoichiometry(self, annotations: list[dict[str, Any]]) -> str | None:
        for annotation in annotations:
            term = self._string_or_none(annotation.get("term"))
            if term == str(ParticipantMetadataCv.STOICHIOMETRY):
                return self._string_or_none(annotation.get("value"))
        return None

    def _derive_direction(
        self,
        annotations: list[dict[str, Any]],
        entity_a_annotations: list[dict[str, Any]],
        entity_b_annotations: list[dict[str, Any]],
    ) -> int | None:
        for annotation in annotations:
            term = self._string_or_none(annotation.get("term"))
            value = (self._string_or_none(annotation.get("value")) or "").upper()
            if term == str(InteractionMetadataCv.CONVERSION_DIRECTION):
                if value in {"LEFT_TO_RIGHT", "RIGHT_TO_LEFT", "FORWARD", "BACKWARD"}:
                    return 1
                if value == "REVERSIBLE":
                    return 0
                return 1 if value else None

        a_roles = {self._string_or_none(item.get("term")) for item in entity_a_annotations if item.get("term")}
        b_roles = {self._string_or_none(item.get("term")) for item in entity_b_annotations if item.get("term")}
        if str(ParticipantMetadataCv.SOURCE) in a_roles and str(ParticipantMetadataCv.TARGET) in b_roles:
            return 1
        if str(BiologicalRoleCv.CONTROLLER) in a_roles and str(BiologicalRoleCv.CONTROLLED) in b_roles:
            return 1
        if str(BiologicalRoleCv.REACTANT) in a_roles and str(BiologicalRoleCv.PRODUCT) in b_roles:
            return 1
        return None

    def _derive_sign(self, annotations: list[dict[str, Any]]) -> int | None:
        for annotation in annotations:
            term = self._string_or_none(annotation.get("term"))
            value = (self._string_or_none(annotation.get("value")) or "").upper()
            if term in SIGN_POSITIVE_TERMS:
                return 1
            if term in SIGN_NEGATIVE_TERMS:
                return -1
            if term == str(InteractionMetadataCv.CONTROL_TYPE):
                if "ACTIV" in value or "POSITIVE" in value:
                    return 1
                if "INHIB" in value or "NEGATIVE" in value or "REPRESS" in value:
                    return -1
        return None

    def _order_interaction_members(self, members: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        if len(members) != 2:
            raise ValueError("interaction ordering requires exactly 2 members")
        first, second = members
        first_terms = {self._string_or_none(item.get("term")) for item in first["membership_annotations"] if item.get("term")}
        second_terms = {self._string_or_none(item.get("term")) for item in second["membership_annotations"] if item.get("term")}

        source_like = {
            str(ParticipantMetadataCv.SOURCE),
            str(BiologicalRoleCv.CONTROLLER),
            str(BiologicalRoleCv.REACTANT),
            str(BiologicalRoleCv.TEMPLATE),
        }
        target_like = {
            str(ParticipantMetadataCv.TARGET),
            str(BiologicalRoleCv.CONTROLLED),
            str(BiologicalRoleCv.PRODUCT),
        }
        if first_terms & source_like and second_terms & target_like:
            return first, second
        if second_terms & source_like and first_terms & target_like:
            return second, first
        return first, second

    def _cv_term_from_annotation(self, annotation: dict[str, Any]) -> str | None:
        term = self._string_or_none(annotation.get("term"))
        value = self._string_or_none(annotation.get("value"))
        if term == str(IdentifierNamespaceCv.CV_TERM_ACCESSION) and value and self._is_cv_term_accession(value):
            return self._format_cv_term(value)
        if term and value is None and self._is_cv_term_accession(term):
            return self._format_cv_term(term)
        return None

    def _format_cv_term(self, accession: str) -> str:
        return format_cv_term(accession) or accession

    def _is_cv_term_accession(self, value: str) -> bool:
        return bool(
            ACCESSION_RE.match(value)
            or WIKIPATHWAYS_RE.match(value)
            or REACTOME_RE.match(value)
        )

    def _normalize_attribute_term(self, term: str) -> str:
        if self._is_cv_term_accession(term):
            return self._format_cv_term(term)
        return term

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def resolve_silver_dir(source: str, silver_dir: Path | None, inputs_package: str, test_mode: bool = False) -> Path:
    if silver_dir is None:
        silver_dir = default_silver_dir(source)
    return ensure_silver_dir(
        silver_dir=silver_dir,
        source_name=source,
        inputs_package=inputs_package,
        test_mode=test_mode,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert per-source silver parquet into the target parquet schema.")
    parser.add_argument("sources", nargs="+", help="Source module(s) to process, e.g. signor reactome")
    parser.add_argument("--output-root", type=Path, default=Path("data_v2/gold"), help="Root directory for converted per-source outputs (default: data_v2/gold)")
    parser.add_argument("--silver-dir", type=Path, help="Optional explicit silver dir (single-source runs only)")
    parser.add_argument("--inputs-package", default="pypath.inputs_v2")
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--resolver-mapping-dir", type=Path, default=Path("id_resolver/data"), help="Resolver mapping directory for authoritative canonicalization (default: id_resolver/data)")
    parser.add_argument("--test-mode", action="store_true", help="Build silver in test mode if silver output is missing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.silver_dir is not None and len(args.sources) != 1:
        raise SystemExit("--silver-dir can only be used with a single source")

    canonicalization_summaries: dict[str, dict[str, object]] = {}
    for source in args.sources:
        source_silver_dir = resolve_silver_dir(
            source,
            args.silver_dir if len(args.sources) == 1 else None,
            args.inputs_package,
            test_mode=args.test_mode,
        )
        source_output_dir = args.output_root / source
        source_output_dir.mkdir(parents=True, exist_ok=True)

        converter = SourceConverter(
            source=source,
            silver_dir=source_silver_dir,
            output_dir=source_output_dir,
            batch_size=args.batch_size,
        )
        try:
            converter.convert()
        finally:
            converter.close()
        canonicalize_summary = normalize_target_schema_dir(
            source_dir=source_output_dir,
            mapping_dir=args.resolver_mapping_dir,
            source_name=source,
        )
        dedup_summary = deduplicate_target_schema_dir(source_output_dir)
        canonicalization_summaries[source] = canonicalize_summary
        print(f"[{source}] wrote target tables to {source_output_dir} (canonicalize: {canonicalize_summary}, dedup: {dedup_summary})")

    write_canonicalization_overview_report(args.output_root, source_summaries=canonicalization_summaries)
    return 0
