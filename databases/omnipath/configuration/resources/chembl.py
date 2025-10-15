from omnipath_build.utils.silver_schema import SilverEntity
from pypath.inputs.chembl import molecule

__all__ = [
    'chembl_molecules',
]
def chembl_molecules():
    for rec in molecule():
        annotations = []

        # Add molecule metadata annotations
        if rec.molecule_type:
            annotations.append({"term": "molecule_type", "value": rec.molecule_type})
        if rec.structure_type:
            annotations.append({"term": "structure_type", "value": rec.structure_type})
        if rec.chirality:
            annotations.append({"term": "chirality", "value": rec.chirality})
        if rec.natural_flag is not None:
            annotations.append({"term": "natural_flag", "value": rec.natural_flag})
        if rec.polymer_flag is not None:
            annotations.append({"term": "polymer_flag", "value": rec.polymer_flag})
        if rec.biotherapeutic is not None:
            annotations.append({"term": "biotherapeutic", "value": rec.biotherapeutic})
        if rec.helm_notation:
            annotations.append({"term": "helm_notation", "value": rec.helm_notation})

        # Add molecule properties - convert NamedTuple to annotation dicts
        if rec.molecule_properties:
            props_dict = rec.molecule_properties._asdict()
            for key, value in props_dict.items():
                if value is not None:
                    annotations.append({"term": key, "value": value})

        yield SilverEntity(
            source='ChembL',
            entity_type='compound',
            accession=rec.molecule_chembl_id,
            inchikey=rec.structure.inchi_key if rec.structure else None,
            inchi=rec.structure.inchi if rec.structure else None,
            smiles=rec.structure.canonical_smiles if rec.structure else None,
            name=rec.preferred_name,
            annotations=annotations if annotations else None,
        )
