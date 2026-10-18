
import hou
import json
import os

# ============================================================
# SETTINGS
# ============================================================

OUTPUT_DIR = hou.expandString("$HIP")
OUTPUT_FILE = "houdini_network_export.json"


# ============================================================
# JSON HELPERS
# ============================================================

def safe_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    try:
        if isinstance(value, hou.Vector3):
            return list(value)
    except Exception:
        pass

    if isinstance(value, (list, tuple)):
        return [safe_value(v) for v in value]

    try:
        return str(value)
    except Exception:
        return None


def enum_name(value):
    """Convert a Houdini enum to a stable string."""
    return str(value).split(".")[-1]


# ============================================================
# RAMP EXPORT
# ============================================================

def export_ramp(parm):
    """
    Export ramp control points, positions, values and interpolation
    bases using Houdini's ramp API.
    """

    try:
        ramp = parm.evalAsRamp()

        return {
            "type": "ramp",
            "basis": [enum_name(b) for b in ramp.basis()],
            "keys": [float(k) for k in ramp.keys()],
            "values": [safe_value(v) for v in ramp.values()],
        }

    except Exception:
        return None


# ============================================================
# PARAMETER EXPORT
# ============================================================

def export_parm(parm):
    data = {
        "name": parm.name(),
        "kind": "parameter",
        "value": None,
        "expression": None,
        "expression_language": None,
        "keyframes": [],
    }

    # Try ramp export first. Non-ramp parameters fall through.
    ramp_data = export_ramp(parm)

    if ramp_data is not None:
        data.update(ramp_data)
        return data

    try:
        keyframes = parm.keyframes()

        if keyframes:
            data["kind"] = "keyframed"

            for key in keyframes:
                item = {
                    "time": safe_value(key.time()),
                    "value": None,
                    "expression": None,
                    "expression_language": None,
                }

                try:
                    item["value"] = safe_value(key.value())
                except Exception:
                    pass

                try:
                    item["expression"] = key.expression()
                    item["expression_language"] = enum_name(
                        key.expressionLanguage()
                    )
                except Exception:
                    pass

                data["keyframes"].append(item)

        else:
            try:
                data["value"] = safe_value(parm.eval())
            except Exception:
                pass

            try:
                data["expression"] = parm.expression()
                data["expression_language"] = enum_name(
                    parm.expressionLanguage()
                )
            except Exception:
                pass

    except Exception as exc:
        data["error"] = str(exc)

    return data


# ============================================================
# FLAG EXPORT
# ============================================================

def export_flags(node):
    flags = {}

    methods = {
        "display": "isDisplayFlagSet",
        "render": "isRenderFlagSet",
        "bypass": "isBypassed",
        "locked": "isLockedHDA",
        "template": "isTemplateFlagSet",
    }

    for name, method_name in methods.items():
        try:
            method = getattr(node, method_name, None)

            if callable(method):
                flags[name] = bool(method())
        except Exception:
            pass

    return flags


# ============================================================
# CONNECTION EXPORT
# ============================================================

def export_connections(node):
    inputs = []
    outputs = []

    try:
        for conn in node.inputConnections():
            source = conn.inputNode()

            inputs.append({
                "input_index": conn.inputIndex(),
                "source_node": source.path() if source else None,
                "source_output_index": conn.outputIndex(),
            })
    except Exception:
        pass

    try:
        for conn in node.outputConnections():
            target = conn.outputNode()

            outputs.append({
                "output_index": conn.outputIndex(),
                "target_node": target.path() if target else None,
                "target_input_index": conn.inputIndex(),
            })
    except Exception:
        pass

    # Preserve empty input slots.
    try:
        connected = {
            item["input_index"] for item in inputs
        }

        for index in range(len(node.inputs())):
            if index not in connected:
                inputs.append({
                    "input_index": index,
                    "source_node": None,
                    "source_output_index": None,
                })
    except Exception:
        pass

    inputs.sort(key=lambda item: item["input_index"])

    return inputs, outputs


# ============================================================
# NODE EXPORT
# ============================================================

def export_node(node):
    try:
        node_type = node.type().name()
        category = node.type().category().name()
    except Exception:
        node_type = "unknown"
        category = "unknown"

    try:
        position = list(node.position())
    except Exception:
        position = None

    try:
        color = list(node.color().rgb())
    except Exception:
        color = None

    inputs, outputs = export_connections(node)

    data = {
        "name": node.name(),
        "path": node.path(),
        "type": node_type,
        "type_category": category,
        "position": position,
        "comment": node.comment(),
        "color": color,
        "flags": export_flags(node),
        "parameters": [],
        "inputs": inputs,
        "outputs": outputs,
        "children": [],
    }

    try:
        for parm in node.parms():
            try:
                data["parameters"].append(export_parm(parm))
            except Exception as exc:
                data["parameters"].append({
                    "name": parm.name(),
                    "error": str(exc),
                })
    except Exception:
        pass

    try:
        for child in node.children():
            data["children"].append(export_node(child))
    except Exception:
        pass

    return data


# ============================================================
# MAIN
# ============================================================

def main():
    selected = hou.selectedNodes()

    if len(selected) != 1:
        raise hou.Error(
            "Select exactly one root node or subnet to export."
        )

    root = selected[0]

    network_data = {
        "format": "houdini_network_export",
        "version": 2,
        "houdini_version": hou.applicationVersionString(),
        "root_node": root.path(),
        "network": export_node(root),
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    output_path = os.path.join(
        OUTPUT_DIR, OUTPUT_FILE
    )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            network_data,
            f,
            indent=4,
            ensure_ascii=False,
            allow_nan=False,
        )

    print("=" * 60)
    print("HOUDINI NETWORK EXPORT COMPLETE")
    print("Root: {}".format(root.path()))
    print("File: {}".format(output_path))
    print("Version: 2 (ramp-aware)")
    print("=" * 60)


main()
