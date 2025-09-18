# tests/test_export_success.py
import json
from export import Exporter
from state import (
    NewSection, StartComponent, SetFieldValue, NextField,
    NavPage, SetSectionLength
)

def test_export_success_minimal_flow(store, registry, tmp_path):
    # Section 1: length 64, Gas Heater L / Rack(2)
    store.apply(NewSection(name="S1", length=64))
    store.apply(StartComponent(token="gas"))
    store.apply(SetFieldValue("L"))   # Handing = Left
    store.apply(NextField())
    store.apply(SetFieldValue("2"))   # Heater Size = Rack
    store.apply(NextField())          # Commit

    # Section 2: length 30, EC Fans Right / No / No
    store.apply(NewSection(name="S2", length=30))
    store.apply(StartComponent(token="ec"))
    store.apply(SetFieldValue("R"))   # Mounting location = Right
    store.apply(NextField())
    store.apply(SetFieldValue("N"))   # Backdraft dampers = No
    store.apply(NextField())
    store.apply(SetFieldValue("N"))   # Vertically mounted = No
    store.apply(NextField())          # Commit

    # Export
    xp = Exporter(registry)
    data = xp.build(store.state)

    # Validate
    ok, errs = xp.validate(data)
    assert ok, f"Expected valid export, got errors: {errs}"

    # Shape assertions (spot-check)
    assert data["Unit Properties"]["Unit size"]["Section quantity"] == 2
    sections = data["Unit Properties"]["Unit size"]["Section length"]
    assert sections[0]["Section Number"] == 1
    assert sections[0]["Length"] == 64
    assert sections[1]["Section Number"] == 2
    assert sections[1]["Length"] == 30

    comp1 = sections[0]["Components"][0]
    assert comp1["Label"] == "Gas Heater"
    assert "GasHeater" in comp1
    assert comp1["GasHeater"]["Handing"] == "Left"
    assert comp1["GasHeater"]["Heater Size"] == "Rack"

    comp2 = sections[1]["Components"][0]
    assert comp2["Label"] == "EC Fans"
    assert "ECM" in comp2
    assert comp2["ECM"]["Mounting location"] == "Right"
    assert comp2["ECM"]["Backdraft dampers"] == "No"
    assert comp2["ECM"]["Vertically mounted"] == "No"

    # Filename templating (uses pdf path + page)
    out_name = xp.filename(store.state, "{tag}_p{page}.json")
    assert out_name.endswith("AHU-23_p1.json")

    # Write once to ensure JSON dumps ok
    out_path = tmp_path / "export.json"
    out_path.write_text(xp.dumps(data, pretty=True), encoding="utf-8")
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded["Unit Properties"]["Unit size"]["Section quantity"] == 2
