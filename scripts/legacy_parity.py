"""Parser parity: the legacy pypath input and its inputs_v2 port, side by side.

For each resource, run the legacy function and the inputs_v2 datasets, reduce
both to comparable keys in the source's own identifiers, and report what only
one side has. Both sides read the same cached file and neither translates
identifiers, so a difference is a difference in parsing (each result lists
the files each side read, with their checksums).

Keys by kind:
- relation: (subject, object) with the sign (+, -, 0 or none) compared on the
  pairs both sides have;
- complex: the set of member identifiers;
- keyed_complex: the complex's own identifier, then its members where they
  are plain identifiers on both sides (legacy expands nested complexes and
  families into every combination of their members, inputs_v2 keeps them as
  references);
- annotation: (entity, value).

Nothing is downloaded: both sides read the files in PYPATH_DOWNLOAD_DATADIR
(the build's download directory), and a legacy input that asks for a file the
inputs_v2 module does not have fails with NotCached. Each resource runs in its
own process. The legacy inputs need pycurl, which the project environment does
not install. On nicesrv, from the build checkout:

    PYPATH_DOWNLOAD_DATADIR=/root/data/pypath-cache \
        uv run --frozen --with pycurl python scripts/legacy_parity.py \
        trrust corum --out /root/data/scratch/parity/out
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
from typing import Any, Literal

SAMPLE = 25

Kind = Literal['relation', 'complex', 'keyed_complex', 'annotation']


# Setup ---------------------------------------------------------------------
# Nothing is downloaded. inputs_v2 opens the files already in the build's
# download directory; a legacy download is served the inputs_v2 file of the
# same request (URL and POST data), and any other legacy download fails.

_files: list[dict[str, Any]] = []
_served: dict[tuple, str] = {}


class NotCached(RuntimeError):
    """A download the cache cannot serve."""


def _sha256(path: str | os.PathLike) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _record_file(side: str, url: str, path: str | None) -> None:
    entry = {'side': side, 'url': url, 'path': path}
    if path and os.path.isfile(path):
        entry['size'] = os.path.getsize(path)
        entry['sha256'] = _sha256(path)
        entry['mtime'] = time.strftime(
            '%Y-%m-%d', time.localtime(os.path.getmtime(path))
        )
    if entry not in _files:
        _files.append(entry)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _request(url: str, post: Any = None) -> tuple:
    """A download request as both generations can spell it."""
    url = re.sub(r'^https?://', '', url.strip()).rstrip('/')
    if isinstance(post, dict):
        post = post.items()
    params = frozenset((_text(k), _text(v)) for k, v in post or ())
    return url, params


def serve(module: str, aliases: dict[str, str] | None = None) -> None:
    """Let legacy downloads read the files of an inputs_v2 module."""
    import importlib

    from pypath.share.downloads import get_data_dir

    data_dir = get_data_dir()
    resource = importlib.import_module(f'pypath.inputs_v2.{module}').resource
    for dataset in resource.datasets().values():
        download = dataset.download
        if download is None or callable(download.url) or callable(download.filename):
            continue
        kwargs = download.download_kwargs or {}
        post = kwargs.get('query') if kwargs.get('post') else None
        path = str(data_dir / download.subfolder / download.filename)
        _served[_request(download.url, post)] = path
    for legacy_url, v2_url in (aliases or {}).items():
        for (url, params), path in list(_served.items()):
            if url == _request(v2_url)[0]:
                _served[_request(legacy_url)[0], params] = path


def setup(scratch: Path) -> None:
    """Make both input generations importable, offline, logging the files read."""
    from omnipath_build.cachedir_compat import patch_cachedir_opener

    patch_cachedir_opener()

    # The legacy package writes its own caches; keep them out of the build's
    # download directory.
    import pypath.share.cache as legacy_cache

    legacy_dir = scratch / 'legacy-cache'
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_cache.get_cachedir = lambda *a, **k: legacy_dir

    # Module-level lookups of the legacy package that call services whose
    # formats changed; nothing compared here depends on them.
    import pypath.inputs.unichem as unichem
    import pypath.inputs.mirbase as mirbase

    unichem.unichem_sources = lambda *a, **k: {}
    mirbase.mirbase_organisms = lambda *a, **k: {}

    # Organism names, without the Ensembl and NCBI tables the legacy lookups
    # download.
    import pypath.utils.taxonomy as taxonomy

    organisms = {
        9606: ('human', 'Homo sapiens', 'hs'),
        10090: ('mouse', 'Mus musculus', 'mm'),
        10116: ('rat', 'Rattus norvegicus', 'rn'),
    }
    by_name = {
        name.lower(): taxid for taxid, names in organisms.items() for name in names
    }
    by_name |= {'murine': 10090}

    def ensure_ncbi_tax_id(taxon):
        if taxon is None:
            return None
        if isinstance(taxon, int) or str(taxon).isdigit():
            return int(taxon)
        return by_name.get(str(taxon).strip().lower())

    def ensure_common_name(taxon, lower=False):
        taxid = ensure_ncbi_tax_id(taxon)
        name = organisms[taxid][0] if taxid in organisms else None
        return name if lower or name is None else name.capitalize()

    taxonomy.ensure_ncbi_tax_id = ensure_ncbi_tax_id
    taxonomy.ensure_common_name = ensure_common_name
    taxonomy.ensure_latin_name = lambda t: organisms.get(ensure_ncbi_tax_id(t), (None, None))[1]

    from cachedir._open import Opener
    import pypath.inputs_v2.base as v2_base
    from pypath.share.downloads import get_data_dir
    import pypath.share.curl as curl

    def cached_download_and_open(
        url, filename, subfolder, large=True, encoding='utf-8',
        default_mode='r', ext=None, needed=None, **_download_kwargs,
    ):
        path = get_data_dir() / subfolder / filename
        if not path.is_file() or not path.stat().st_size:
            raise NotCached(f'inputs_v2 file not in the cache: {path} ({url})')
        _record_file('v2', url, str(path))
        return Opener(
            path=str(path), ext=ext, needed=needed, large=large,
            default_mode=default_mode, encoding=encoding,
        )

    v2_base.download_and_open = cached_download_and_open

    curl_init = curl.Curl.__init__

    def cached_curl_init(self, url, *args, **kwargs):
        if kwargs.get('setup', True) is False or os.path.exists(url):
            return curl_init(self, url, *args, **kwargs)
        post = kwargs.get('binary_data') or kwargs.get('post')
        path = _served.get(_request(url, post))
        if path is None or not os.path.isfile(path):
            raise NotCached(
                f'legacy download with no inputs_v2 file: {url} {post or ""}'.strip()
            )
        _record_file('legacy', url, path)
        kwargs.pop('binary_data', None)
        kwargs.pop('post', None)
        curl_init(self, path, *args, **kwargs)

    curl.Curl.__init__ = cached_curl_init


# Keys ----------------------------------------------------------------------

_ISOFORM = re.compile(r'^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})-\d+$')


def tokens(value: Any) -> frozenset[str]:
    """Spellings of one identifier that count as the same: without a CURIE
    prefix, upper case, and a UniProt isoform also as its canonical entry."""
    if value is None:
        return frozenset()
    value = str(value).strip()
    if not value:
        return frozenset()
    out = {value.upper()}
    if ':' in value:
        out.add(value.split(':', 1)[1].strip().upper())
    for v in list(out):
        if match := _ISOFORM.match(v):
            out.add(match.group(1))
    return frozenset(out)


def id_tokens(values: Iterable[Any]) -> frozenset[str]:
    return frozenset().union(*(tokens(v) for v in values)) if values else frozenset()


def sign_of(value: Any) -> str | None:
    """+, -, 0 or None from the many ways inputs say it."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return '+' if value > 0 else '-' if value < 0 else '0'
    text = str(value).strip().lower()
    if not text or text in {'nan', 'none', 'unknown', '?'}:
        return None
    if text in {'1', '+', '+1'}:
        return '+'
    if text in {'-1', '-'}:
        return '-'
    if text in {'0'}:
        return '0'
    negative = ('down', 'inhib', 'decreas', 'repress', 'negativ', 'antagon', 'destab')
    positive = ('up', 'activ', 'stimul', 'increas', 'induc', 'positiv', 'agon')
    has_negative = any(word in text for word in negative)
    has_positive = any(word in text for word in positive) and not has_negative
    if has_negative and not any(text.startswith(w) for w in positive):
        return '-'
    if has_positive:
        return '+'
    if 'unknown' in text or 'unspecified' in text or 'neutral' in text:
        return '0'
    return None


@dataclass
class Rel:
    subject: frozenset[str]
    object: frozenset[str]
    sign: str | None = None
    label: str = ''


@dataclass
class Spec:
    name: str
    kind: Kind
    legacy: Callable[[], Iterable[Any]]
    v2: Callable[[], Iterable[Any]]
    # The inputs_v2 module whose files the legacy downloads are served.
    serve: str = ''
    aliases: dict[str, str] = field(default_factory=dict)
    note: str = ''


# inputs_v2 records -----------------------------------------------------------

def name(term: Any) -> str:
    """A Biolink slot, enum member or CV term as its plain name."""
    for attr in ('name', 'text'):
        value = getattr(term, attr, None)
        if isinstance(value, str):
            return value.replace(' ', '_')
    return str(getattr(term, 'value', term))


def _ref_ids(ref) -> list[str]:
    if hasattr(ref, 'identifiers'):
        return [i.value for i in ref.identifiers or ()]
    return [getattr(ref, 'identifier', None)]


def _ref_taxa(ref) -> set[str]:
    return {
        name(a.value).removeprefix('NCBITaxon:')
        for a in getattr(ref, 'annotations', None) or ()
        if name(a.term).endswith('in_taxon')
    }


# Aspects whose direction is a regulatory sign. A decreased phosphorylation
# (a phosphatase) says nothing about activation or inhibition, and the legacy
# inputs give no sign for it.
_SIGNED_ASPECTS = {None, 'activity', 'abundance', 'activity_or_abundance', 'expression', 'stability'}


def _v2_sign(relation) -> str | None:
    annotations = {
        name(a.term): name(a.value) for a in relation.annotations or ()
    }
    if annotations.get('object_aspect_qualifier') not in _SIGNED_ASPECTS:
        return None
    if 'object_direction_qualifier' in annotations:
        return sign_of(annotations['object_direction_qualifier'])
    return sign_of(name(relation.predicate).rsplit(':', 1)[-1].replace('_', ' '))


def v2_relations(records, taxon: str | None = None) -> Iterable[Rel]:
    for relation in records:
        if relation is None:  # a row the mapper drops
            continue
        if taxon and any(
            (taxa := _ref_taxa(ref)) and taxon not in taxa
            for ref in (relation.subject, relation.object)
        ):
            continue
        yield Rel(
            id_tokens(_ref_ids(relation.subject)),
            id_tokens(_ref_ids(relation.object)),
            _v2_sign(relation),
            label=f'{_ref_ids(relation.subject)} -[{name(relation.predicate)}]-> {_ref_ids(relation.object)}',
        )


def _primary_id(entity, prefer: str) -> str | None:
    identifiers = list(getattr(entity, 'identifiers', None) or ())
    for identifier in identifiers:
        if prefer in name(identifier.type).lower():
            return identifier.value
    return identifiers[0].value if identifiers else getattr(entity, 'identifier', None)


def v2_complexes(records, prefer: str = 'uniprot', taxon: str | None = None) -> Iterable[frozenset[str]]:
    for entity in records:
        if entity is None:
            continue
        members = [m.member for m in entity.membership or () if not m.is_parent]
        if taxon and any((t := _ref_taxa(m)) and taxon not in t for m in members):
            continue
        ids = {_primary_id(m, prefer) for m in members}
        ids.discard(None)
        if ids:
            yield frozenset(min(tokens(i), key=len) for i in ids)


def datasets(module: str, *names: str, **kwargs) -> Callable[[], Iterable[Any]]:
    def run():
        import importlib

        resource = importlib.import_module(f'pypath.inputs_v2.{module}').resource
        for name in names:
            yield from getattr(resource, name)(**kwargs)

    return run


# Comparison ----------------------------------------------------------------

def compare_relations(legacy: Iterable[Rel], v2: Iterable[Rel]) -> dict[str, Any]:
    # Each legacy record with every spelling of its pair (prefix, isoform,
    # abbreviation); records with the same spellings are one pair.
    legacy_records: dict[frozenset, set] = defaultdict(set)
    for rel in legacy:
        pairs = frozenset((a, b) for a in rel.subject for b in rel.object)
        if pairs:
            legacy_records[pairs].add(rel.sign)
    legacy_signs = {pair for pairs in legacy_records for pair in pairs}

    v2_total = 0
    v2_unmatched: list[str] = []
    v2_reversed: list[str] = []
    v2_ids: set[str] = set()
    matched: set[tuple[str, str]] = set()
    sign_pairs: dict[tuple[str, str], set] = defaultdict(set)
    v2_signs = Counter()
    for rel in v2:
        v2_total += 1
        v2_ids |= rel.subject | rel.object
        v2_signs[rel.sign] += 1
        hits = {(a, b) for a in rel.subject for b in rel.object if (a, b) in legacy_signs}
        if hits:
            matched |= hits
            for hit in hits:
                sign_pairs[hit].add(rel.sign)
        elif any((b, a) in legacy_signs for a in rel.subject for b in rel.object):
            v2_reversed.append(rel.label)
        else:
            v2_unmatched.append(rel.label)

    # A legacy record is found when any spelling of its pair matched.
    found = [rec for rec in legacy_records if rec & matched]
    missing = [rec for rec in legacy_records if not rec & matched]

    sign_agree = sign_disagree = 0
    disagreements = []
    for rec in found:
        pair = next(iter(rec & matched))
        # A sign of 0 states no direction, as no sign does.
        legacy_sign = legacy_records[rec] - {None, '0'}
        v2_sign = {s for p in rec & matched for s in sign_pairs[p]} - {None, '0'}
        if not legacy_sign or not v2_sign:
            continue
        if legacy_sign == v2_sign:
            sign_agree += 1
        else:
            sign_disagree += 1
            if len(disagreements) < SAMPLE:
                disagreements.append(
                    {'pair': list(pair), 'legacy': sorted(legacy_sign), 'v2': sorted(v2_sign)}
                )

    def known(rec):
        return max((a in v2_ids) + (b in v2_ids) for a, b in rec)

    missing_known = Counter(known(rec) for rec in missing)
    return {
        'legacy_pairs': len(legacy_records),
        'legacy_pairs_found_in_v2': len(found),
        'legacy_pairs_missing_in_v2': len(missing),
        'missing_by_ids_known_to_v2': {
            'both': missing_known[2], 'one': missing_known[1], 'neither': missing_known[0],
        },
        'v2_relations': v2_total,
        'v2_relations_without_legacy_pair': len(v2_unmatched),
        'v2_relations_reversed_in_legacy': len(v2_reversed),
        'v2_signs': {str(k): v for k, v in v2_signs.most_common()},
        'signs_compared': sign_agree + sign_disagree,
        'signs_agree': sign_agree,
        'signs_disagree': sign_disagree,
        'samples': {
            'legacy_missing_in_v2': [list(min(r)) for r in missing[:SAMPLE]],
            'v2_without_legacy_pair': v2_unmatched[:SAMPLE],
            'v2_reversed_in_legacy': v2_reversed[:SAMPLE],
            'sign_disagreements': disagreements,
        },
    }


def compare_sets(legacy: Iterable[Any], v2: Iterable[Any], what: str) -> dict[str, Any]:
    legacy_set = set(legacy)
    v2_set = set(v2)
    both = legacy_set & v2_set
    legacy_only = sorted(legacy_set - v2_set, key=str)
    v2_only = sorted(v2_set - legacy_set, key=str)
    show = sorted if what == 'complex' else list
    result = {
        'legacy_keys': len(legacy_set),
        'v2_keys': len(v2_set),
        'both': len(both),
        'legacy_only': len(legacy_only),
        'v2_only': len(v2_only),
        'samples': {
            'legacy_only': [show(x) for x in legacy_only[:SAMPLE]],
            'v2_only': [show(x) for x in v2_only[:SAMPLE]],
        },
    }
    if what == 'complex':
        # A complex whose member set differs slightly is not a missing complex.
        v2_members = set().union(*v2_set) if v2_set else set()
        result['legacy_only_with_all_members_in_v2'] = sum(
            1 for c in legacy_only if c <= v2_members
        )
    return result


def compare_keyed_complexes(legacy: Iterable[tuple], v2: Iterable[tuple]) -> dict[str, Any]:
    legacy_by_id: dict[str, set] = defaultdict(set)
    for key, members in legacy:
        legacy_by_id[key].add(members)
    v2_by_id = dict(v2)
    shared = sorted(legacy_by_id.keys() & v2_by_id.keys())
    nested = [k for k in shared if any(m.startswith('SIGNOR-') for m in v2_by_id[k])]
    flat = [k for k in shared if k not in set(nested)]
    differ = [k for k in flat if v2_by_id[k] not in legacy_by_id[k]]
    return {
        'legacy_ids': len(legacy_by_id),
        'v2_ids': len(v2_by_id),
        'both': len(shared),
        'legacy_only': len(legacy_by_id.keys() - v2_by_id.keys()),
        'v2_only': len(v2_by_id.keys() - legacy_by_id.keys()),
        'both_with_nested_members': len(nested),
        'both_flat_members_equal': len(flat) - len(differ),
        'both_flat_members_differ': len(differ),
        'samples': {
            'legacy_only': sorted(legacy_by_id.keys() - v2_by_id.keys())[:SAMPLE],
            'v2_only': sorted(v2_by_id.keys() - legacy_by_id.keys())[:SAMPLE],
            'members_differ': [
                {
                    'id': k,
                    'legacy': [sorted(m) for m in legacy_by_id[k]][:3],
                    'v2': sorted(v2_by_id[k]),
                }
                for k in differ[:SAMPLE]
            ],
        },
    }


def v2_keyed_complexes(records, id_namespace: str, prefer: str = 'uniprot') -> Iterable[tuple]:
    for entity in records:
        if entity is None:
            continue
        key = _primary_id(entity, id_namespace)
        members = [m.member for m in entity.membership or () if not m.is_parent]
        ids = {_primary_id(m, prefer) for m in members} - {None}
        yield key, frozenset(min(tokens(i), key=len) for i in ids)


# Resources -----------------------------------------------------------------
# Each legacy adapter reads the legacy function's least processed output, so
# that neither side has translated identifiers.

def _complex_tokens(cplex) -> frozenset[str]:
    return frozenset(min(tokens(c), key=len) for c in cplex.components)


def _signor_interactions():
    from pypath.inputs import signor

    for row in signor.signor_interactions(raw_records=True):
        yield Rel(tokens(row['IDA']), tokens(row['IDB']), sign_of(row['EFFECT']))


def _signor_complexes():
    from pypath.inputs import signor

    for cplex in signor.signor_complexes().values():
        for key in cplex.ids['SIGNOR']:
            yield key, _complex_tokens(cplex)


def _signalink():
    from pypath.inputs import signalink

    for rec in signalink.signalink_interactions(exclude_secondary=False):
        yield Rel(tokens(rec.id_a), tokens(rec.id_b), sign_of(rec.effect))


def _collectri():
    from pypath.inputs import collectri

    for rec in collectri.collectri_raw(mirna=True):
        yield Rel(tokens(rec.tf), tokens(rec.target), sign_of(rec.effect))


def _dorothea():
    from pypath.inputs import dorothea

    for rec in dorothea.dorothea_full_raw().itertuples():
        yield Rel(tokens(rec.tf), tokens(rec.target), sign_of(int(rec.mor)))


def _trrust():
    from pypath.inputs import trrust

    for rec in trrust.trrust_interactions('human'):
        yield Rel(
            tokens(rec.source_genesymbol), tokens(rec.target_genesymbol),
            sign_of(rec.effect),
        )


def _corum():
    from pypath.inputs import corum

    for cplex in corum.corum_complexes(9606).values():
        yield _complex_tokens(cplex)


def _phosphosite():
    from pypath.inputs import phosphosite

    for rec in phosphosite.phosphosite_enzyme_substrate(raw=True, organism='human'):
        yield Rel(tokens(rec['kinase']), tokens(rec['substrate']))


def _complexportal():
    from pypath.inputs import complexportal

    for cplex in complexportal.complexportal_complexes(9606).values():
        yield _complex_tokens(cplex)


def _cellphonedb():
    from pypath.inputs import cellphonedb

    def ids(partner):
        if hasattr(partner, 'ids'):
            return id_tokens(i for ids in partner.ids.values() for i in ids)
        return tokens(partner)

    for rec in cellphonedb.cellphonedb_interactions():
        yield Rel(ids(rec.id_a), ids(rec.id_b))


def _cellchat():
    from pypath.inputs import cellchatdb
    from pypath.inputs_v2.cellchat import metabolite

    frame = cellchatdb.cellchatdb_download(9606)['interaction']
    for rec in frame.itertuples():
        # A metabolite ligand ("PGE2-PTGES") is the metabolite in inputs_v2,
        # which keeps CellChat's abbreviation as a synonym.
        ligand = tokens(rec.ligand)
        if rec.annotation == 'Non-protein Signaling' and (abbreviation := metabolite(rec.ligand)):
            ligand |= tokens(abbreviation)
        yield Rel(ligand, tokens(rec.receptor))


def _matrisome():
    from pypath.inputs import matrisome

    for uniprot, annotations in matrisome.matrisome_annotations(9606).items():
        for annotation in annotations:
            yield (uniprot, annotation.mainclass)


def _v2_matrisome():
    for relation in datasets('matrisome', 'human')():
        if relation is None:
            continue
        for gene in _ref_ids(relation.subject)[:2]:
            yield (gene, relation.object.identifier)


SPECS: dict[str, Spec] = {}


def spec(*args, **kwargs):
    s = Spec(*args, **kwargs)
    SPECS[s.name] = s
    return s


spec(
    'signor', 'relation', _signor_interactions,
    lambda: v2_relations(datasets('signor', 'interactions')(), taxon='9606'),
    serve='signor',
    note='Legacy reads the CSV export of human interactions; inputs_v2 reads the causalTab export.',
)
spec(
    'signor_complexes', 'keyed_complex', _signor_complexes,
    lambda: v2_keyed_complexes(datasets('signor', 'complexes')(), 'signor'),
    serve='signor',
)
spec(
    'signalink', 'relation', _signalink,
    lambda: v2_relations(datasets('signalink', 'interactions')(), taxon='9606'),
    serve='signalink',
    note='Legacy with secondary sources included, as inputs_v2 has them; human pairs only.',
)
spec(
    'collectri', 'relation', _collectri,
    lambda: v2_relations(datasets('collectri', 'interactions')()),
    serve='collectri',
    note='Legacy collectri_raw with miRNA targets, before translation to UniProt.',
)
spec(
    'dorothea', 'relation', _dorothea,
    lambda: v2_relations(datasets('dorothea', 'level_a', 'level_b', 'level_c', 'level_d', 'level_e')()),
    serve='dorothea',
    note='All confidence levels on both sides.',
)
spec(
    'trrust', 'relation', _trrust,
    lambda: v2_relations(datasets('trrust', 'human_interactions')()),
    serve='trrust',
)
spec(
    'corum', 'complex', _corum,
    lambda: v2_complexes(datasets('corum', 'complexes')(), taxon='9606'),
    serve='corum',
)
spec(
    'phosphosite', 'relation', _phosphosite,
    lambda: v2_relations(datasets('phosphosite', 'enzyme_substrate')(), taxon='9606'),
    serve='phosphosite',
    note='Kinase-substrate pairs with both proteins human; legacy states no sign.',
)
spec(
    'complexportal', 'complex', _complexportal,
    lambda: v2_complexes(datasets('complexportal', 'human_complexes')()),
    serve='complexportal',
    note='Legacy reads the PSI-MI XML archive; inputs_v2 reads ComplexTAB.',
)
spec(
    'cellphonedb', 'relation', _cellphonedb,
    lambda: v2_relations(datasets('cellphonedb', 'interactions')()),
    serve='cellphonedb',
)
spec(
    'cellchat', 'relation', _cellchat,
    lambda: v2_relations(datasets('cellchat', 'human_interactions')()),
    serve='cellchat',
    aliases={
        'https://raw.githubusercontent.com/jinworks/CellChat/master/data/CellChatDB.human.rda':
        'https://raw.githubusercontent.com/jinworks/CellChat/main/data/CellChatDB.human.rda',
    },
    note=(
        'Legacy side is the interaction table of the R data file (its processing needs '
        'UniProt mapping); a metabolite ligand label also matches its abbreviation.'
    ),
)
spec(
    'matrisome', 'annotation', _matrisome, _v2_matrisome,
    serve='matrisome',
    note='Legacy reads the rescued XLS master list; inputs_v2 reads the current XLSX.',
)


# Driver --------------------------------------------------------------------

def run(spec: Spec) -> dict[str, Any]:
    """Compare one resource. inputs_v2 runs first: some legacy modules patch
    shared packages (pypath.inputs.rdata changes the rdata converter)."""
    _files.clear()
    _served.clear()
    serve(spec.serve, spec.aliases)
    result: dict[str, Any] = {'resource': spec.name, 'kind': spec.kind, 'note': spec.note}
    outputs = {}
    for side, produce in (('v2', spec.v2), ('legacy', spec.legacy)):
        started = time.time()
        try:
            outputs[side] = list(produce())
        except Exception as error:
            result['error'] = f'{side}: {error!r}'
            result['traceback'] = traceback.format_exc()
            result['files'] = list(_files)
            return result
        result[f'{side}_seconds'] = round(time.time() - started, 1)
    legacy, v2 = outputs['legacy'], outputs['v2']
    if spec.kind == 'relation':
        result |= compare_relations(legacy, v2)
    elif spec.kind == 'keyed_complex':
        result |= compare_keyed_complexes(legacy, v2)
    else:
        result |= compare_sets(legacy, v2, spec.kind)
    result['files'] = list(_files)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('resources', nargs='*', help=f'default: all of {", ".join(SPECS)}')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--in-process', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    names = args.resources or list(SPECS)
    if not args.in_process:
        # One process per resource, so that no legacy module's patches reach
        # another resource.
        for name in names:
            subprocess.run(
                [sys.executable, __file__, name, '--out', str(args.out), '--in-process'],
                check=False,
            )
        return

    setup(args.out)
    for name in names:
        print(f'== {name}', file=sys.stderr, flush=True)
        result = run(SPECS[name])
        (args.out / f'{name}.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
        summary = {k: v for k, v in result.items() if k not in {'samples', 'files', 'traceback'}}
        print(json.dumps(summary, indent=2, default=str), file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
