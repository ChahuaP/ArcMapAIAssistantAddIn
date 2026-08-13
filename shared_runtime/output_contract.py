# -*- coding: utf-8 -*-
from __future__ import absolute_import


class OutputContractError(Exception):
    pass


_WRITE_POLICY_KEYS = frozenset((
    "writes_output",
    "type",
    "formats",
    "default_format",
    "workspace",
    "overwrite",
    "add_to_map",
    "geometry_type",
))


def validate_output_policy(policy, side_effects):
    if not isinstance(policy, dict):
        raise OutputContractError("output_policy must be an object.")
    if side_effects != "writes_data":
        return dict(policy)

    unknown = sorted(set(policy) - _WRITE_POLICY_KEYS)
    if unknown:
        raise OutputContractError(
            "writes_data output_policy has unknown fields: %s." % ", ".join(unknown)
        )
    required = _WRITE_POLICY_KEYS - frozenset(("geometry_type",))
    missing = sorted(required - set(policy))
    if missing:
        raise OutputContractError(
            "writes_data output_policy is missing fields: %s." % ", ".join(missing)
        )
    if policy["writes_output"] is not True:
        raise OutputContractError("writes_data output_policy.writes_output must be true.")
    output_type = policy["type"]
    if output_type == "feature_class":
        expected_formats, expected_workspace, expected_add = ["gdb"], "server_managed_gdb", True
    elif output_type == "file" and policy["formats"] in (["csv"], ["png"]):
        expected_formats, expected_workspace, expected_add = policy["formats"], "server_managed_files", False
    else:
        raise OutputContractError("writes_data output_policy type/format is unsupported.")
    if policy["formats"] != expected_formats:
        raise OutputContractError("writes_data output_policy formats are invalid.")
    if policy["default_format"] != expected_formats[0]:
        raise OutputContractError("writes_data default_format must equal its sole format.")
    if policy["workspace"] != expected_workspace:
        raise OutputContractError("writes_data output_policy workspace is invalid.")
    if policy["overwrite"] is not False:
        raise OutputContractError("feature_class output_policy.overwrite must be false.")
    if policy["add_to_map"] is not expected_add:
        raise OutputContractError("writes_data output_policy.add_to_map is invalid.")
    return dict(policy)


def output_policy_type(policy):
    if not isinstance(policy, dict):
        raise OutputContractError("output_policy must be an object.")
    value = policy.get("type")
    if value not in ("feature_class", "file"):
        raise OutputContractError("output_policy.type must be feature_class or file.")
    return value
