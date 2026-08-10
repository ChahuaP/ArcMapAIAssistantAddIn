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
    if policy["type"] != "feature_class":
        raise OutputContractError("writes_data output_policy.type must be feature_class.")
    if policy["formats"] != ["gdb"]:
        raise OutputContractError("feature_class output_policy.formats must be ['gdb'].")
    if policy["default_format"] != "gdb":
        raise OutputContractError("feature_class output_policy.default_format must be gdb.")
    if policy["workspace"] != "mxd_default_or_output_workspace":
        raise OutputContractError(
            "feature_class output_policy.workspace must be mxd_default_or_output_workspace."
        )
    if policy["overwrite"] is not False:
        raise OutputContractError("feature_class output_policy.overwrite must be false.")
    if policy["add_to_map"] is not True:
        raise OutputContractError("feature_class output_policy.add_to_map must be true.")
    return dict(policy)


def output_policy_type(policy):
    if not isinstance(policy, dict):
        raise OutputContractError("output_policy must be an object.")
    value = policy.get("type")
    if value != "feature_class":
        raise OutputContractError("output_policy.type must be feature_class.")
    return value
