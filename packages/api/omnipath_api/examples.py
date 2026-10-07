"""Small release-specific presentation cache for the explorer landing page."""

import logging
import hashlib
import json
import os
import tempfile
from pathlib import Path


logger = logging.getLogger(__name__)

# Ordered suggestions, with exact label alternatives. Missing examples are skipped.
SUGGESTIONS = [
    (("aspirin",), "chemical_entity", ""),
    (("glucose", "d-glucose"), "chemical_entity", ""),
    (("caffeine",), "chemical_entity", ""),
    (("atp", "adenosine triphosphate"), "chemical_entity", ""),
    (("cholesterol",), "chemical_entity", ""),
    (("lactate", "l-lactate"), "chemical_entity", ""),
    (("tp53",), "protein", "9606"),
    (("egfr",), "protein", "9606"),
    (("akt1",), "protein", "9606"),
    (("ins", "insulin"), "protein", "9606"),
    (("glycolysis", "glycolysis / gluconeogenesis"), "pathway", ""),
    (("apoptosis",), "pathway", ""),
]


def get_examples(engine):
    infos = engine._selected_resource_infos()
    identity = [
        [info["key"] for info in infos],
        engine._inventory_fingerprint,
        SUGGESTIONS,
        engine._release_scope.get(),
        engine._taxonomy_cache_version(),
        [m.get("references") for m in engine.releases.list()],
    ]
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()

    def load():
        target = engine.data_root / ".presentation" / "examples-v1" / (key + ".json")
        try:
            return json.loads(target.read_text())
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            logger.warning("Cannot load example cache %s", target, exc_info=True)
        entities = []
        if infos:
            wanted = [
                (rank, label, kind, taxon)
                for rank, (labels, kind, taxon) in enumerate(SUGGESTIONS)
                for label in labels
            ]
            placeholders = ",".join("(?,?,?,?)" for _ in wanted)
            read = engine._table("entity")
            rows = engine._db.execute(
                f"""WITH wanted(rank,label,kind,taxon) AS (VALUES {placeholders})
                SELECT wanted.rank, e.entity_key FROM {read} e JOIN wanted
                ON lower(e.label)=wanted.label AND e.entity_type=wanted.kind
                   AND (wanted.taxon='' OR e.taxon=wanted.taxon)
                QUALIFY row_number() OVER (PARTITION BY wanted.rank ORDER BY
                    CASE WHEN e.namespace IN ('uniprot','inchikey') THEN 0 ELSE 1 END, e.entity_key)=1
                ORDER BY wanted.rank""",
                [v for row in wanted for v in row],
            ).fetchall()
            keys = list(dict.fromkeys(row[1] for row in rows))
            hydrated = {
                row["entity_key"]: row for row in engine._fetch_entities_by_keys(keys, slim=True)
            }
            entities = [engine._to_entity_summary(hydrated[key]) for key in keys if key in hydrated]
        result = dict(entities=entities, nextCursor=None, total=len(entities), kind="examples")
        if engine.read_only:
            return result
        # A read-only data mount can still serve/cache examples in memory.
        staging = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as handle:
                staging = Path(handle.name)
                json.dump(result, handle)
            os.replace(staging, target)
        except OSError:
            logger.warning("Cannot persist optional example cache %s", target, exc_info=True)
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)
        return result

    return engine._example_cache.get(key, load)
