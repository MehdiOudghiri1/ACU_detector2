from registry import PluginRegistry


def test_resolve_token_and_get_spec():
    reg = PluginRegistry()
    # Aliases should map to type_id
    assert reg.resolve_token("ec") == "ECM"
    assert reg.resolve_token("fan") == "ECM"
    assert reg.resolve_token("gas") == "GasHeater"
    assert reg.resolve_token("filters") == "Filters"

    # type_id and label are also valid tokens
    assert reg.resolve_token("ECM") == "ECM"
    assert reg.resolve_token("ec fans") == "ECM"

    # get_spec returns the schema dict
    ecm = reg.get_spec("ECM")
    assert ecm["label"] == "EC Fans"
    assert ecm["field_sequence"] == ["mounting_location", "backdraft_dampers", "vertically_mounted"]
    assert "fields" in ecm and "mounting_location" in ecm["fields"]


def test_normalization_enum_bool_int(registry):
    # enum: ECM mounting
    ok, val, err = registry.validate_value("ECM", "mounting_location", "r")
    assert ok and val == "Right"

    ok, val, err = registry.validate_value("ECM", "mounting_location", "Left")
    assert ok and val == "Left"

    # bool: ECM backdraft_dampers
    ok, val, err = registry.validate_value("ECM", "backdraft_dampers", "y")
    assert ok and val == "Yes"

    ok, val, err = registry.validate_value("ECM", "backdraft_dampers", 0)
    assert ok and val == "No"

    # int: Misc quantities
    ok, val, err = registry.validate_value("Misc", "lights_qty", "9")
    assert ok and val == 9

    ok, val, err = registry.validate_value("PlateHEX", "stack_qty", "0")
    assert not ok and "Minimum" in err  # min=1
