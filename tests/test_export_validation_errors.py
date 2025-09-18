# tests/test_export_validation_errors.py
from export import Exporter
from state import (
    NewSection, StartComponent, SetFieldValue, NextField
)

def test_export_missing_required_field_blocks_save(store, registry):
    # Create Section 1
    store.apply(NewSection(name="S1", length=64))

    # Start Gas Heater but DO NOT set heater_size
    store.apply(StartComponent(token="gas"))
    store.apply(SetFieldValue("L"))   # Handing=Left
    # Move to next field (heater_size) but don't set it; try to export anyway.
    store.apply(NextField())

    xp = Exporter(registry)
    data = xp.build(store.state)
    ok, errs = xp.validate(data)

    assert not ok, "Validation should fail when required fields are missing."
    # Should contain a targeted message about 'Heater Size'
    assert any("Heater Size" in e for e in errs), f"Expected Heater Size error, got: {errs}"
