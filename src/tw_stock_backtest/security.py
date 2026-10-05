"""Output safeguards without reading or comparing actual credential values."""
import os
import re

SENSITIVE_KEY = re.compile(r'password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key', re.I)
ASSIGNMENT = re.compile(
    r'(?i)((?:[\w-]*(?:password|passwd|secret|token|api[_-]?key|authorization)[\w-]*)'
    r'[\"\x27]?\s*[:=]\s*)(?:\"[^\"\r\n]*\"|\x27[^\x27\r\n]*\x27|[^\s,;]+)')
TOKEN = re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AIza[\w-]{30,}|(?:AKIA|ASIA)[A-Z0-9]{16})\b')
PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----', re.S)
RUNTIME_ENV = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR', 'TEMP', 'TMP',
               'SYSTEMROOT', 'MPLCONFIGDIR', 'PYTHONPATH', 'PYTHONIOENCODING', 'PYTHONUTF8')


def safe_text(value):
    value = PRIVATE_KEY.sub('[REDACTED PRIVATE KEY]', value)
    value = TOKEN.sub('[REDACTED]', value)
    return ASSIGNMENT.sub(lambda m: m.group(1) + '[REDACTED]', value)


def public_data(value):
    if isinstance(value, dict):
        return {key: '[REDACTED]' if SENSITIVE_KEY.search(str(key)) else public_data(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [public_data(item) for item in value]
    return safe_text(value) if isinstance(value, str) else value


def child_environment():
    # Do not enumerate credentials and then filter them: only read runtime keys.
    result = {key: os.environ[key] for key in RUNTIME_ENV if key in os.environ}
    result['MPLBACKEND'] = 'Agg'
    return result
