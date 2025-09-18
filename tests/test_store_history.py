from state import Store
from state import NewSection, StartComponent, SetFieldValue, NextField


def test_store_apply_undo_redo(registry):
    store = Store(registry=registry)

    store.apply(NewSection(name="S1", length=64))
    store.apply(StartComponent(token="gas"))
    store.apply(SetFieldValue("L"))
    store.apply(NextField())
    store.apply(SetFieldValue("2"))
    store.apply(NextField())  # auto-commit

    assert len(store.state.sections[0].components) == 1

    store.undo()
    assert len(store.state.sections[0].components) == 0

    store.redo()
    assert len(store.state.sections[0].components) == 1
