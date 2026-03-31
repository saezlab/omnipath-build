#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.package_emitter.config import default_silver_dir
from omnipath_build.package_emitter.silver import ensure_silver_dir
from scripts.target_schema_entity_dedup import deduplicate_target_schema_dir
from pypath.internals.cv_terms import (
    BiologicalEffectCv,
    BiologicalRoleCv,
    CausalMechanismCv,
    CausalStatementCv,
    CvEnum,
    ExperimentalRoleCv,
    IdentifierNamespaceCv,
    InteractionMetadataCv,
    InteractionTypeCv,
    ParticipantMetadataCv,
)
from pypath.internals.cv_terms.entity_types import EntityTypeCv


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
    pa.field("identifier_type_id", pa.string()),
    pa.field("is_canonical", pa.bool_()),
    pa.field("source", pa.string()),
])

ENTITIES_SCHEMA = pa.schema([
    pa.field("entity_id", pa.int64()),
    pa.field("entity_type_id", pa.string()),
    pa.field("display_name", pa.string()),
    pa.field("canonical_identifier", pa.string()),
    pa.field("canonical_identifier_type_id", pa.string()),
    pa.field("taxonomy_id", pa.string()),
    pa.field("source", pa.string()),
])

INTERACTIONS_SCHEMA = pa.schema([
    pa.field("interaction_id", pa.int64()),
    pa.field("entity_a_id", pa.int64()),
    pa.field("entity_b_id", pa.int64()),
    pa.field("direction", pa.int64()),
    pa.field("sign", pa.int64()),
    pa.field("mechanism_term", pa.string()),
    pa.field("statement_term", pa.string()),
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
INTERACTION_LIKE_TYPES = {
    str(EntityTypeCv.INTERACTION),
    str(EntityTypeCv.REACTION),
    str(EntityTypeCv.CATALYSIS),
    str(EntityTypeCv.CONTROL),
    str(EntityTypeCv.DEGRADATION),
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
STATEMENT_TERMS = {str(term) for term in CausalStatementCv} | {str(term) for term in InteractionTypeCv}
MECHANISM_TERMS = {str(term) for term in CausalMechanismCv}
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


def _iter_cv_subclasses(base: type) -> Iterable[type]:
    for subcls in base.__subclasses__():
        yield subcls
        yield from _iter_cv_subclasses(subcls)


def _humanize_enum_name(name: str) -> str:
    return name.replace("_", " ").title()


def _build_cv_label_map() -> dict[str, str]:
    labels: dict[str, str] = {}
    for enum_cls in _iter_cv_subclasses(CvEnum):
        for member in enum_cls:
            labels.setdefault(str(member), _humanize_enum_name(member.name))
    return labels


CV_LABELS = _build_cv_label_map()


@dataclass
class EntityRef:
    entity_id: int
    canonical_identifier: str | None
    canonical_identifier_type_id: str | None
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
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is None:
            pq.write_table(pa.Table.from_pylist([], schema=self.schema), self.path)
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
        self.entity_identifiers = BufferedParquetWriter(output_dir / "entity_identifiers.parquet", ENTITY_IDENTIFIERS_SCHEMA, batch_size)
        self.interactions = BufferedParquetWriter(output_dir / "interactions.parquet", INTERACTIONS_SCHEMA, batch_size)
        self.associations = BufferedParquetWriter(output_dir / "associations.parquet", ASSOCIATIONS_SCHEMA, batch_size)
        self.annotations = BufferedParquetWriter(output_dir / "annotations.parquet", ANNOTATIONS_SCHEMA, batch_size)

    def close(self) -> None:
        self.entities.close()
        self.entity_identifiers.close()
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

    def _process_entity_row(self, row: dict[str, Any]) -> EntityRef:
        parent = self._materialize_entity(row)
        parent_type = self._string_or_none(row.get("type"))

        memberships = row.get("membership") or []
        if not memberships:
            if parent_type not in INTERACTION_LIKE_TYPES:
                self._emit_cv_annotations("entity", parent.entity_id, row.get("annotations") or [])
            return parent

        members: list[dict[str, Any]] = []
        for membership in memberships:
            member_row = membership.get("member") or {}
            member_ref = self._materialize_entity(member_row)
            membership_annotations = membership.get("annotations") or []
            merged_member_attrs = self._merge_attributes(
                member_ref.entity_attributes,
                self._annotations_to_attributes(membership_annotations),
            )
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

        if (parent_type in INTERACTION_LIKE_TYPES) and len(members) == 2:
            interaction_id = self.next_interaction_id
            self.next_interaction_id += 1

            entity_a, entity_b = self._order_interaction_members(members)
            self.interactions.write({
                "interaction_id": interaction_id,
                "entity_a_id": entity_a["ref"].entity_id,
                "entity_b_id": entity_b["ref"].entity_id,
                "direction": self._derive_direction(parent_annotations, entity_a["membership_annotations"], entity_b["membership_annotations"]),
                "sign": self._derive_sign(parent_annotations),
                "mechanism_term": self._derive_mechanism_term(parent_annotations),
                "statement_term": self._derive_statement_term(parent_annotations),
                "record_attributes": non_evidence,
                "entity_a_attributes": entity_a["member_attributes"],
                "entity_b_attributes": entity_b["member_attributes"],
                "evidence": evidence,
                "source": self.source,
            })
            self._emit_cv_annotations("interaction", interaction_id, parent_annotations)
        else:
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
                    "parent_attributes": parent.entity_attributes,
                    "member_attributes": member["member_attributes"],
                    "evidence": evidence,
                    "source": self.source,
                })
            if parent_type not in INTERACTION_LIKE_TYPES:
                self._emit_cv_annotations("entity", parent.entity_id, parent_annotations)

        return parent

    def _materialize_entity(self, row: dict[str, Any]) -> EntityRef:
        entity_id = self.next_entity_id
        self.next_entity_id += 1

        identifiers = [ident for ident in (row.get("identifiers") or []) if ident and ident.get("value")]
        canonical = self._choose_canonical_identifier(identifiers)
        display_name = self._choose_display_name(identifiers, canonical)
        taxonomy_id = self._extract_taxonomy_id(
            row.get("annotations") or [],
            identifiers,
            row.get("membership") or [],
        )
        entity_attributes = self._annotations_to_attributes(row.get("annotations") or [], evidence_only=False)

        self.entities.write({
            "entity_id": entity_id,
            "entity_type_id": self._string_or_none(row.get("type")),
            "display_name": display_name,
            "canonical_identifier": canonical[1] if canonical else None,
            "canonical_identifier_type_id": canonical[0] if canonical else None,
            "taxonomy_id": taxonomy_id,
            "source": self.source,
        })

        for ident in identifiers:
            ident_type = self._string_or_none(ident.get("type"))
            ident_value = self._string_or_none(ident.get("value"))
            self.entity_identifiers.write({
                "entity_id": entity_id,
                "identifier": ident_value,
                "identifier_type_id": ident_type,
                "is_canonical": canonical is not None and ident_type == canonical[0] and ident_value == canonical[1],
                "source": self.source,
            })

        return EntityRef(
            entity_id=entity_id,
            canonical_identifier=canonical[1] if canonical else None,
            canonical_identifier_type_id=canonical[0] if canonical else None,
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

    def _choose_canonical_identifier(self, identifiers: list[dict[str, Any]]) -> tuple[str, str] | None:
        if not identifiers:
            return None

        preferred = [
            str(IdentifierNamespaceCv.NAME),
            str(IdentifierNamespaceCv.GENE_NAME_PRIMARY),
            str(IdentifierNamespaceCv.UNIPROT),
            str(IdentifierNamespaceCv.CHEBI),
            str(IdentifierNamespaceCv.REACTOME_STABLE_ID),
            str(IdentifierNamespaceCv.BINDINGDB),
            str(IdentifierNamespaceCv.SIGNOR),
        ]
        by_type = {self._string_or_none(item.get("type")): self._string_or_none(item.get("value")) for item in identifiers}
        for ident_type in preferred:
            ident_value = by_type.get(ident_type)
            if ident_value:
                return ident_type, ident_value

        first = identifiers[0]
        ident_type = self._string_or_none(first.get("type"))
        ident_value = self._string_or_none(first.get("value"))
        if ident_type and ident_value:
            return ident_type, ident_value
        return None

    def _choose_display_name(self, identifiers: list[dict[str, Any]], canonical: tuple[str, str] | None) -> str | None:
        for preferred_type in (str(IdentifierNamespaceCv.NAME), str(IdentifierNamespaceCv.GENE_NAME_PRIMARY), str(IdentifierNamespaceCv.SYSTEMATIC_NAME)):
            for ident in identifiers:
                if self._string_or_none(ident.get("type")) == preferred_type and ident.get("value"):
                    return self._string_or_none(ident.get("value"))
        return canonical[1] if canonical else None

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
                if term == str(IdentifierNamespaceCv.NCBI_TAX_ID):
                    continue
                if value is None:
                    continue

            rows.append({
                "term": self._normalize_attribute_term(term),
                "value": value,
                "unit": unit,
            })
        return rows or None

    def _merge_attributes(
        self,
        left: list[dict[str, str | None]] | None,
        right: list[dict[str, str | None]] | None,
    ) -> list[dict[str, str | None]] | None:
        merged = list(left or [])
        merged.extend(right or [])
        return merged or None

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

    def _derive_mechanism_term(self, annotations: list[dict[str, Any]]) -> str | None:
        for annotation in annotations:
            term = self._string_or_none(annotation.get("term"))
            if term in MECHANISM_TERMS:
                return self._format_cv_term(term)
        return None

    def _derive_statement_term(self, annotations: list[dict[str, Any]]) -> str | None:
        for annotation in annotations:
            term = self._string_or_none(annotation.get("term"))
            if term in STATEMENT_TERMS:
                return self._format_cv_term(term)
        for annotation in annotations:
            term = self._string_or_none(annotation.get("term"))
            value = self._string_or_none(annotation.get("value"))
            if term == str(InteractionMetadataCv.CONTROL_TYPE) and value:
                return value
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
        if term == str(IdentifierNamespaceCv.CV_TERM_ACCESSION) and value and ACCESSION_RE.match(value):
            return self._format_cv_term(value)
        if term and value is None and ACCESSION_RE.match(term):
            return self._format_cv_term(term)
        return None

    def _format_cv_term(self, accession: str) -> str:
        label = CV_LABELS.get(accession)
        if label is None:
            label = accession
        return f"{accession}:{label}"

    def _normalize_attribute_term(self, term: str) -> str:
        if ACCESSION_RE.match(term):
            return self._format_cv_term(term)
        return term

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def resolve_silver_dir(source: str, silver_dir: Path | None, inputs_package: str) -> Path:
    if silver_dir is None:
        silver_dir = default_silver_dir(source)
    return ensure_silver_dir(silver_dir=silver_dir, source_name=source, inputs_package=inputs_package)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert per-source silver parquet into the target parquet schema.")
    parser.add_argument("sources", nargs="+", help="Source module(s) to process, e.g. signor reactome")
    parser.add_argument("--output-root", type=Path, required=True, help="Root directory for converted per-source outputs")
    parser.add_argument("--silver-dir", type=Path, help="Optional explicit silver dir (single-source runs only)")
    parser.add_argument("--inputs-package", default="pypath.inputs_v2")
    parser.add_argument("--batch-size", type=int, default=10_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.silver_dir is not None and len(args.sources) != 1:
        raise SystemExit("--silver-dir can only be used with a single source")

    for source in args.sources:
        source_silver_dir = resolve_silver_dir(
            source,
            args.silver_dir if len(args.sources) == 1 else None,
            args.inputs_package,
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
        dedup_summary = deduplicate_target_schema_dir(source_output_dir)
        print(f"[{source}] wrote target tables to {source_output_dir} (dedup: {dedup_summary})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
