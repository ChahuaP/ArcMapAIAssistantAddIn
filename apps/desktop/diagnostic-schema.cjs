const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const revision = /^\d{1,5}\.\d{1,5}\.\d{1,5}$/;
const formats = ['shp', 'gpkg', 'geojson', 'tif', 'tiff', 'img', 'dxf', 'dwg', 'pdf', 'docx', 'xlsx', 'txt', 'doc', 'xls', 'zip', 'other'];
const codes = {
    material_read: ['timeout', 'reader_failure'],
    dispatch: ['model_auth', 'model_limit', 'model_missing', 'model_network', 'tool_not_called', 'no_result',
        'timeout', 'interrupted', 'budget_exhausted', 'operation_failed', 'agent_failure'],
    publish: ['result_read_failed'],
    desktop: ['renderer_gone'],
};
const statuses = ['failed', 'interrupted', 'timed_out', 'budget_exhausted', 'no_result'];
const providers = ['minimax-cn', 'deepseek', 'openai', 'anthropic', 'custom'];
const skills = ['control-check', 'farmland-balance', 'shared-delivery', 'cad-coordinates', 'annual-land',
    'permit-history', 'imagery-change', 'custom'];
const modelNames = /^(?:MiniMax-|deepseek-|gpt-|chatgpt-|claude-|o[1-9])[A-Za-z0-9.-]{0,65}$/i;
const keys = ['schema', 'id', 'time', 'version', 'os', 'arch', 'stage', 'status', 'code', 'taskId', 'dispatchId',
    'runId', 'elapsedMs', 'provider', 'mode', 'model', 'skill', 'formats', 'materialCount'];

function validReport(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !keys.includes(key))) return false;
    const { schema, id, time, version, os, arch, stage, status, code, taskId, dispatchId, runId,
        elapsedMs, provider, mode, model, skill, formats: extensions, materialCount } = value;
    const optional = (item, check) => item === undefined || check(item);
    const matches = pattern => item => typeof item === 'string' && pattern.test(item);
    const boundedInt = max => item => Number.isInteger(item) && item >= 0 && item <= max;
    return schema === 1 && matches(uuid)(id) && matches(revision)(version) && matches(revision)(os) &&
        matches(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/)(time) && Number.isFinite(Date.parse(time)) &&
        ['x64', 'arm64', 'ia32'].includes(arch) && Object.hasOwn(codes, stage) && codes[stage].includes(code) && statuses.includes(status) &&
        optional(taskId, matches(uuid)) && optional(dispatchId, matches(uuid)) && optional(runId, matches(/^[a-f0-9]{32}$/)) &&
        optional(elapsedMs, boundedInt(900_000)) && optional(provider, item => providers.includes(item)) &&
        optional(mode, item => ['local', 'online'].includes(item)) &&
        optional(model, item => provider !== 'custom' && matches(modelNames)(item)) && optional(skill, item => skills.includes(item)) &&
        optional(extensions, item => Array.isArray(item) && item.length <= formats.length && item.every(format => formats.includes(format))) &&
        optional(materialCount, boundedInt(32));
}

module.exports = { validReport, uuid, formats, modelNames, skills };
