import importlib.machinery, importlib.util
from pathlib import Path

loader=importlib.machinery.SourceFileLoader("sudogemin_master",str(Path(__file__).resolve().parents[1]/"master"))
spec=importlib.util.spec_from_loader(loader.name,loader)
module=importlib.util.module_from_spec(spec)
loader.exec_module(module)

def test_merkle_is_deterministic():
    rows=[{"id":1},{"id":2}]
    assert module.merkle_root(rows)==module.merkle_root(rows)

def test_merkle_is_order_sensitive():
    rows=[{"id":1},{"id":2}]
    assert module.merkle_root(rows)!=module.merkle_root(list(reversed(rows)))

def test_merkle_changes_on_mutation():
    assert module.merkle_root([{"x":1}])!=module.merkle_root([{"x":2}])

def test_empty_tree_domain_is_distinct():
    assert module.merkle_root([])!=module.merkle_root([{}])

def test_merkle_inclusion_proof():
    rows=[{"id":i} for i in range(5)]
    for i,row in enumerate(rows):
        proof=module.merkle_proof(rows,i)
        assert module.verify_merkle_proof(row,proof,module.merkle_root(rows))
