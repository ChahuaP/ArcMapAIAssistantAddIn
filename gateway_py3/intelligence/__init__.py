"""Intelligence layer: ModelRuntime, prompts, TaskCompiler, WorkflowEngine.

Target architecture §9 places these under ``intelligence/``. Stage B implements
ModelRuntime (§6.4): the single owner of MiniMax adapter access, prompt
rendering, the exact-result cache, the call ledger and quota-stop policy.
"""
