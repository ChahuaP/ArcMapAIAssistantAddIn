"""Model-facing execution evidence; full receipts remain in the operation journal."""

# These are storage/transport details or duplicated pre/post snapshots. Keep
# statuses, output paths, independent check results and failure explanations.
_AUDIT_FIELDS = frozenset({
    'members', 'input_snapshot', 'observation', 'receipt', 'traceback',
    'lease_id', 'epoch', 'plan_hash', 'deployment_hash', 'result_hash',
})


def model_result(value):
    if isinstance(value, dict):
        return {key: model_result(item) for key, item in value.items()
                if key not in _AUDIT_FIELDS}
    if isinstance(value, list):
        return [model_result(item) for item in value]
    return value
