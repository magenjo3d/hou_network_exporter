
import hou
import json
import os

# ============================================================
# SETTINGS
# ============================================================

CREATE_WRAPPER_SUBNET = False
WRAPPER_NAME = "imported_network"
RECONNECT_EXTERNAL_NODES = True

NODE_MAP = {}
WARNINGS = []
ERRORS = []


# ============================================================
# HELPERS
# ============================================================

def log_warning(message):
    WARNINGS.append(str(message))
    print("WARNING:", message)


def log_error(message):
    ERRORS.append(str(message))
    print("ERROR:", message)


def unique_node_name(parent, name):
    name = name or "imported_node"

    if parent.node(name) is None:
        return name

    i = 1
    while parent.node("{}_{}".format(name, i)) is not None:
        i += 1

    return "{}_{}".format(name, i)


def get_destination():
    selected = hou.selectedNodes()

    if len(selected) == 1:
        candidate = selected[0]
        try:
            candidate.children()
            return candidate
        except Exception:
            pass

    return hou.node("/obj")


def get_expression_language(name):
    name = str(name or "").lower()

    if "python" in name:
        return hou.exprLanguage.Python

    return hou.exprLanguage.Hscript


# ============================================================
# RAMP RECONSTRUCTION
# ============================================================

def restore_ramp(parm, data):
    """
    Reconstruct a ramp from its serialized interpolation bases,
    key positions and values.
    """

    basis_names = data.get("basis", [])
    keys = data.get("keys", [])
    values = data.get("values", [])

    if not keys or len(keys) != len(values):
        raise ValueError(
            "Ramp keys and values are empty or have different lengths."
        )

    if len(basis_names) != len(keys):
        raise ValueError(
            "Ramp basis count does not match the number of keys."
        )

    # Map serialized enum names back to Houdini ramp bases.
    basis_map = {}

    for name in dir(hou.rampBasis):
        if name.startswith("_"):
            continue

        try:
            value = getattr(hou.rampBasis, name)

            if isinstance(value, hou.rampBasis):
                basis_map[name.lower()] = value
        except Exception:
            pass

    basis = []

    for name in basis_names:
        key = str(name).split(".")[-1].lower()

        if key not in basis_map:
            raise ValueError(
                "Unknown Houdini ramp basis: {}".format(name)
            )

        basis.append(basis_map[key])

    # Restore color-ramp values as Vector3 values when appropriate.
    converted_values = []

    for value in values:
        if isinstance(value, list):
            if len(value) == 3:
                converted_values.append(hou.Vector3(value))
            else:
                raise ValueError(
                    "Unexpected vector ramp value: {}".format(value)
                )
        else:
            converted_values.append(float(value))

    ramp = hou.Ramp(
        tuple(basis),
        tuple(float(k) for k in keys),
        tuple(converted_values),
    )

    parm.set(ramp)


# ============================================================
# NODE CREATION
# ============================================================

def create_nodes_recursive(data, parent):
    original_path = data.get("path", "")
    node_name = data.get("name", "imported_node")
    node_type = data.get("type", "")

    if original_path in NODE_MAP:
        return NODE_MAP[original_path]

    try:
        new_node = parent.createNode(
            node_type,
            node_name=unique_node_name(parent, node_name),
            run_init_scripts=True,
        )

        NODE_MAP[original_path] = new_node

        print("Created:", new_node.path(), node_type)

    except Exception as exc:
        log_error(
            "Could not create '{}' ({}): {}".format(
                original_path, node_type, exc
            )
        )
        return None

    try:
        position = data.get("position")

        if position and len(position) >= 2:
            new_node.setPosition(
                hou.Vector2(position[0], position[1])
            )
    except Exception:
        pass

    try:
        color = data.get("color")

        if color and len(color) >= 3:
            new_node.setColor(hou.Color(color[:3]))
    except Exception:
        pass

    try:
        new_node.setComment(data.get("comment", ""))
    except Exception:
        pass

    for child in data.get("children", []):
        create_nodes_recursive(child, new_node)

    return new_node


# ============================================================
# PARAMETER RESTORATION
# ============================================================

def restore_parameter(node, data):
    name = data.get("name")

    if not name:
        return

    parm = node.parm(name)

    if parm is None:
        return

    # Ramps must be restored as hou.Ramp, not as scalar values.
    if data.get("kind") == "ramp" or data.get("type") == "ramp":
        try:
            restore_ramp(parm, data)
        except Exception as exc:
            log_warning(
                "Ramp '{}.{}' could not be restored: {}".format(
                    node.path(), name, exc
                )
            )
        return

    try:
        keyframes = data.get("keyframes", [])

        if keyframes:
            restored = []

            for item in keyframes:
                key = hou.Keyframe()

                if item.get("expression") is not None:
                    key.setExpression(
                        item["expression"],
                        get_expression_language(
                            item.get("expression_language")
                        ),
                    )
                elif item.get("value") is not None:
                    key.setValue(float(item["value"]))

                if item.get("time") is not None:
                    key.setTime(float(item["time"]))

                restored.append(key)

            parm.setKeyframes(restored)
            return

        expression = data.get("expression")

        if expression is not None:
            parm.setExpression(
                expression,
                language=get_expression_language(
                    data.get("expression_language")
                ),
            )
            return

        value = data.get("value")

        if value is not None:
            parm.set(value)

    except Exception as exc:
        log_warning(
            "Parameter '{}.{}' could not be restored: {}".format(
                node.path(), name, exc
            )
        )


def restore_parameters_recursive(data):
    original_path = data.get("path", "")
    node = NODE_MAP.get(original_path)

    if node is not None:
        for parm_data in data.get("parameters", []):
            restore_parameter(node, parm_data)

    for child in data.get("children", []):
        restore_parameters_recursive(child)


# ============================================================
# CONNECTION RESTORATION
# ============================================================

def restore_connections_recursive(data):
    original_path = data.get("path", "")
    node = NODE_MAP.get(original_path)

    if node is not None:
        for connection in data.get("inputs", []):
            source_path = connection.get("source_node")

            if not source_path:
                continue

            source = NODE_MAP.get(source_path)

            if source is None and RECONNECT_EXTERNAL_NODES:
                source = hou.node(source_path)

            if source is None:
                log_warning(
                    "Connection source not found: {}".format(
                        source_path
                    )
                )
                continue

            try:
                node.setInput(
                    int(connection["input_index"]),
                    source,
                    int(connection.get("source_output_index", 0)),
                )
            except Exception as exc:
                log_warning(
                    "Could not connect {}: {}".format(
                        node.path(), exc
                    )
                )

    for child in data.get("children", []):
        restore_connections_recursive(child)


# ============================================================
# FLAG RESTORATION
# ============================================================

def restore_flags_recursive(data):
    node = NODE_MAP.get(data.get("path", ""))

    if node is not None:
        flags = data.get("flags", {})

        methods = {
            "display": "setDisplayFlag",
            "render": "setRenderFlag",
            "bypass": "bypass",
            "template": "setTemplateFlag",
        }

        for name, method_name in methods.items():
            if name not in flags:
                continue

            try:
                method = getattr(node, method_name, None)

                if callable(method):
                    method(bool(flags[name]))
            except Exception:
                pass

    for child in data.get("children", []):
        restore_flags_recursive(child)


# ============================================================
# MAIN IMPORT
# ============================================================

def main():
    global NODE_MAP, WARNINGS, ERRORS

    NODE_MAP = {}
    WARNINGS = []
    ERRORS = []

    json_path = hou.ui.selectFile(
        title="Select Houdini Network JSON",
        file_type=hou.fileType.Any,
        pattern="*.json",
        chooser_mode=hou.fileChooserMode.Read,
    )

    if not json_path:
        print("Import cancelled.")
        return

    json_path = hou.expandString(json_path)

    if not os.path.isfile(json_path):
        hou.ui.displayMessage(
            "JSON file not found:\n{}".format(json_path),
            severity=hou.severityType.Error,
        )
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            export_data = json.load(f)
    except Exception as exc:
        hou.ui.displayMessage(
            "Could not read JSON:\n{}".format(exc),
            severity=hou.severityType.Error,
        )
        return

    if export_data.get("format") != "houdini_network_export":
        hou.ui.displayMessage(
            "Unsupported JSON export format.",
            severity=hou.severityType.Error,
        )
        return

    root_data = export_data.get("network")

    if not isinstance(root_data, dict):
        hou.ui.displayMessage(
            "The JSON has no valid root network.",
            severity=hou.severityType.Error,
        )
        return

    destination = get_destination()

    if destination is None:
        hou.ui.displayMessage(
            "No valid destination network found.",
            severity=hou.severityType.Error,
        )
        return

    if CREATE_WRAPPER_SUBNET:
        try:
            destination = destination.createNode(
                "subnet",
                node_name=unique_node_name(
                    destination, WRAPPER_NAME
                ),
            )
        except Exception as exc:
            hou.ui.displayMessage(
                "Could not create subnet:\n{}".format(exc),
                severity=hou.severityType.Error,
            )
            return

    print("=" * 60)
    print("IMPORTING HOUDINI NETWORK")
    print("File:", json_path)
    print("Destination:", destination.path())
    print("=" * 60)

    # Pass 1: create all nodes.
    new_root = create_nodes_recursive(root_data, destination)

    if new_root is None:
        hou.ui.displayMessage(
            "Could not create the root node. See the Python Shell.",
            severity=hou.severityType.Error,
        )
        return

    # Pass 2: restore parameters, including ramps.
    restore_parameters_recursive(root_data)

    # Pass 3: reconnect nodes.
    restore_connections_recursive(root_data)

    # Pass 4: restore supported flags.
    restore_flags_recursive(root_data)

    # Preserve saved positions rather than automatically relayout.
    try:
        new_root.setSelected(True, clear_all_selected=True)
    except Exception:
        pass

    print("=" * 60)
    print("IMPORT COMPLETE")
    print("Nodes created:", len(NODE_MAP))
    print("Warnings:", len(WARNINGS))
    print("Errors:", len(ERRORS))
    print("Root:", new_root.path())
    print("=" * 60)

    for message in WARNINGS:
        print("WARNING:", message)

    for message in ERRORS:
        print("ERROR:", message)

    hou.ui.displayMessage(
        "Import finished.\n\n"
        "Nodes created: {}\n"
        "Warnings: {}\n"
        "Errors: {}\n\n"
        "Root: {}".format(
            len(NODE_MAP),
            len(WARNINGS),
            len(ERRORS),
            new_root.path(),
        )
    )


main()
