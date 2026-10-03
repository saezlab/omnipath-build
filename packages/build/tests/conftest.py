"""Importable mapper fixture shared by production multiprocessing tests."""

import importlib
import sys
import pytest


@pytest.fixture
def mapper_module(tmp_path, monkeypatch):
    name = "pipeline_mapper_fixture"
    (tmp_path / f"{name}.py").write_text("""
def mapper(row):
    if 'original' in row:
        row['original'] = 'mutated'
    if 'id' in row:
        return {'type':'named_thing','identifiers':[{'type':'fixture','value':row['id']}]}
    def entity(identifier):
        return {'type':'protein','identifiers':[{'type':'uniprot','value':identifier}]}
    return {'subject':entity('P1'),'predicate':'affects','object':entity('P2')}
class Input:
    mapper = staticmethod(mapper)
items = first = second = repeated = Input()
""")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)
    return importlib.import_module(name)
