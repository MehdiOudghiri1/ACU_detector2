import pytest
from state import (
    AppState, Mode,
    NewSection, StartComponent, SetFieldValue, NextField, PrevField,
    CommitComponent,
)
from state import reduce


def test_gas_heater_happy_path(registry):
    s = AppState()
    s = reduce(s, NewSection(name="S1", length=64), registry)

    s = reduce(s, StartComponent(token="gas"), registry)
    assert s.mode == Mode.FIELD_EDITING

    # handing
    s = reduce(s, SetFieldValue("L"), registry)
    # next
    s = reduce(s, NextField(), registry)
    # heater size
    s = reduce(s, SetFieldValue("2"), registry)
    # commit via next-at-end
    s = reduce(s, NextField(), registry)

    assert s.mode == Mode.SECTION_ACTIVE
    comp = s.get_active_section().components[0]
    assert comp.type_id == "GasHeater"
    assert comp.fields == {"handing": "Left", "heater_size": "Rack"}
    assert s.dirty is True


def test_ecm_optional_booleans_can_be_null(registry):
    s = AppState()
    s = reduce(s, NewSection(name="FANS", length=70), registry)

    s = reduce(s, StartComponent(token="ec"), registry)
    # only set required: mounting_location
    s = reduce(s, SetFieldValue("m"), registry)  # Remote
    s = reduce(s, NextField(), registry)         # goto backdraft_dampers
    s = reduce(s, NextField(), registry)         # skip to vertically_mounted
    s = reduce(s, NextField(), registry)         # end -> commit

    comp = s.get_active_section().components[0]
    # booleans may remain None when not answered
    assert comp.fields["mounting_location"] == "Remote"
    assert comp.fields["backdraft_dampers"] is None
    assert comp.fields["vertically_mounted"] is None


def test_prev_field_does_not_clear_value(registry):
    s = AppState()
    s = reduce(s, NewSection(), registry)
    s = reduce(s, StartComponent(token="filters"), registry)
    s = reduce(s, SetFieldValue("panel"), registry)
    s = reduce(s, PrevField(), registry)  # still on same (single) field
    assert s.editing.values["type"] == "Panel"


def test_commit_rejects_when_required_missing(registry):
    s = AppState()
    s = reduce(s, NewSection(name="X", length=10), registry)
    s = reduce(s, StartComponent(token="plate"), registry)  # PlateHEX
    # try to commit without setting stack_qty (required)
    with pytest.raises(ValueError):
        s = reduce(s, CommitComponent(), registry)
