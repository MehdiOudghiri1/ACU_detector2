"""
Built-in specs for ALL ACU components.
Spec shape per type_id:
{
  "label": "Gas Heater",
  "type_key": "GasHeater",
  "field_sequence": [...],
  "required_fields": [...],
  "fields": {
     "<field>": {"type":"enum|bool|int|number", "map":{...}, "min":..., "max":...}
  },
  "aliases": ["token1","token2",...]
}
"""

BUILTIN_SPECS = {
    # ---------- ECM (EC fan arrays) ----------
    "ECM": {
        "label": "EC Fans",
        "type_key": "ECM",
        "field_sequence": ["mounting_location", "backdraft_dampers", "vertically_mounted"],
        "required_fields": ["mounting_location"],
        "fields": {
            "mounting_location": {"type": "enum", "map": {
                "m":"Remote","remote":"Remote",
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
                "e":"End","end":"End",
            }},
            "backdraft_dampers": {"type": "bool"},
            "vertically_mounted": {"type": "bool"},
        },
        "aliases": ["ec","ecm","ecfans","ec_fans","fan","fans"],
    },

    # ---------- DDPL ----------
    "DDPL": {
        "label": "DDPL",
        "type_key": "DDPL",
        "field_sequence": ["vertically_mounted", "vfd_mount", "jbox_mount"],
        "required_fields": [],
        "fields": {
            "vertically_mounted": {"type":"bool"},
            "vfd_mount": {"type": "enum", "map": {
                "m":"Remote","remote":"Remote",
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
                "e":"End","end":"End",
                "n":"None","none":"None",
            }},
            "jbox_mount": {"type": "enum", "map": {
                "m":"Remote","remote":"Remote",
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
                "e":"End","end":"End",
                "n":"None","none":"None",
            }},
        },
        "aliases": ["ddpl","ddlf","ddl","direct_drive_plenum"],
    },

    # ---------- Coils ----------
    "Coil": {
        "label": "Coil",
        "type_key": "Coil",
        "field_sequence": [
            "handing",
            "face_bypass_damper",
            "construction",         # Single / Stacked
            "staggered",            # Yes/No
            "kits_included", "kits_qty", "kits_mount",
            "controllers_included", "controllers_qty", "controllers_mount",
        ],
        "required_fields": ["handing","construction"],
        "fields": {
            "handing": {"type":"enum","map":{
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
            }},
            "face_bypass_damper": {"type":"bool"},
            "construction": {"type":"enum","map":{
                "single":"Single","s":"Single",
                "stacked":"Stacked","st":"Stacked",
            }},
            "staggered": {"type":"bool"},
            "kits_included": {"type":"bool"},
            "kits_qty": {"type":"int","min":0},
            "kits_mount": {"type":"enum","map":{
                "m":"Remote","remote":"Remote",
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
                "e":"End","end":"End",
                "n":"None","none":"None",
            }},
            "controllers_included": {"type":"bool"},
            "controllers_qty": {"type":"int","min":0},
            "controllers_mount": {"type":"enum","map":{
                "m":"Remote","remote":"Remote",
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
                "e":"End","end":"End",
                "n":"None","none":"None",
            }},
        },
        "aliases": ["coil","coils","cw_coil","hw_coil"],
    },

    # ---------- Humidifiers ----------
    "Humidifier": {
        "label": "Humidifier",
        "type_key": "Humidifier",
        "field_sequence": ["qty"],
        "required_fields": [],
        "fields": {
            "qty": {"type":"int","min":0},
        },
        "aliases": ["humidifier","humidifiers","hum"],
    },

    # ---------- Gas Heaters ----------
    "GasHeater": {
        "label": "Gas Heater",
        "type_key": "GasHeater",
        "field_sequence": ["handing","heater_size"],
        "required_fields": ["handing","heater_size"],
        "fields": {
            "handing": {"type":"enum","map":{
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
            }},
            "heater_size": {"type":"enum","map":{
                "1":"Single","single":"Single",
                "2":"Rack","rack":"Rack",
            }},
        },
        "aliases": ["gas","gas_heater","gh"],
    },

    # ---------- Electric Heaters ----------
    "ElectricHeater": {
        "label": "Electric Heater",
        "type_key": "ElectricHeater",
        "field_sequence": ["handing"],
        "required_fields": ["handing"],
        "fields": {
            "handing": {"type":"enum","map":{
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
            }},
        },
        "aliases": ["electric","eh","elec_heater","heater_electric"],
    },

    # ---------- Heat Pipes (WAHP / SBS) ----------
    "HeatPipe": {
        "label": "Heat Pipe",
        "type_key": "HeatPipe",
        "field_sequence": ["handing","type"],
        "required_fields": ["handing","type"],
        "fields": {
            "handing": {"type":"enum","map":{
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
            }},
            "type": {"type":"enum","map":{
                "wahp":"WAHP","wrap":"WAHP","wraparound":"WAHP",
                "sbs":"SBS",
            }},
        },
        "aliases": ["heatpipe","hp","wahp","sbs"],
    },

    # ---------- Plate Heat Exchangers ----------
    "PlateHEX": {
        "label": "Plate Heat Exchanger",
        "type_key": "PlateHEX",
        "field_sequence": ["stack_qty","bypass_dampers"],
        "required_fields": ["stack_qty"],
        "fields": {
            "stack_qty": {"type":"int","min":1,"max":3},
            "bypass_dampers": {"type":"int","min":0,"max":2},
        },
        "aliases": ["plate","phe","plate_hex","plate_exchanger"],
    },

    # ---------- Accubloc ----------
    "Accubloc": {
        "label": "Accubloc",
        "type_key": "Accubloc",
        "field_sequence": ["qty"],
        "required_fields": [],
        "fields": {
            "qty": {"type":"int","min":0},
        },
        "aliases": ["accubloc","ab","accu"],
    },

    # ---------- Wheel Heat Exchangers ----------
    "WheelHEX": {
        "label": "Wheel Heat Exchanger",
        "type_key": "WheelHEX",
        "field_sequence": ["qty","bypass_dampers","vfd_mount"],
        "required_fields": ["qty"],
        "fields": {
            "qty": {"type":"int","min":1,"max":2},
            "bypass_dampers": {"type":"int","min":0,"max":2},
            "vfd_mount": {"type":"enum","map":{
                "l":"Left","left":"Left",
                "r":"Right","right":"Right",
                "e":"End","end":"End",
                "n":"None","none":"None",
            }},
        },
        "aliases": ["wheel","rotary","erw","wheel_hex","rotor"],
    },

    # ---------- UV Lights ----------
    "UVLights": {
        "label": "UV Lights",
        "type_key": "UVLights",
        "field_sequence": ["qty"],
        "required_fields": ["qty"],
        "fields": {
            "qty": {"type":"int","min":1},
        },
        "aliases": ["uv","uv_lights","uvlight","uvs"],
    },

    # ---------- Filters ----------
    "Filters": {
        "label": "Filters",
        "type_key": "Filters",
        "field_sequence": ["type"],
        "required_fields": ["type"],
        "fields": {
            "type": {"type":"enum","map":{
                "panel":"Panel","p":"Panel",
                "combo":"Combo","c":"Combo",
                "angled":"Angled","a":"Angled",
            }},
        },
        "aliases": ["filter","filters","pre","final"],
    },

    # ---------- Misc ----------
    "Misc": {
        "label": "Misc",
        "type_key": "Misc",
        "field_sequence": ["lights_qty","safety_grating_qty","internal_door_qty","afms_qty"],
        "required_fields": [],
        "fields": {
            "lights_qty": {"type":"int","min":0},
            "safety_grating_qty": {"type":"int","min":0},
            "internal_door_qty": {"type":"int","min":0},
            "afms_qty": {"type":"int","min":0},
        },
        "aliases": ["misc","lights","grating","door","afms"],
    },
}
