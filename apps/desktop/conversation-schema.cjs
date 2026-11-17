const { uuid } = require('./diagnostic-schema.cjs');

const maxConversationBytes = 2 * 1024 * 1024;
const conversationRetentionDays = 14;

function validConversation(value) {
    return value && Object.keys(value).length === 6 && value.schema === 1 && typeof value.id === 'string' &&
        uuid.test(value.id) && typeof value.version === 'string' && /^\d{1,5}\.\d{1,5}\.\d{1,5}$/.test(value.version) &&
        typeof value.time === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(value.time) &&
        Number.isFinite(Date.parse(value.time)) && Number.isSafeInteger(value.turns) && value.turns > 0 &&
        typeof value.text === 'string' && Boolean(value.text.trim());
}

module.exports = { validConversation, maxConversationBytes, conversationRetentionDays };
