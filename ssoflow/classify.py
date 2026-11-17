"""登录环节、失败原因与后续动作的归类。

失败原因按所处环节（stage）归类；多因素（mfa）的失败与绕过单独成组。
reason 使用稳定的机器可读代码，reason_text 保留原始/可读描述，方便聚合也方便人读。
"""

# 标准环节。
STAGE_AUTH = "authentication"  # 口令 / 凭据
STAGE_TOKEN = "token"          # 令牌
STAGE_MFA = "mfa"              # 多因素
STAGE_SESSION = "session"      # 会话

STAGES = (STAGE_AUTH, STAGE_TOKEN, STAGE_MFA, STAGE_SESSION)

# 失败原因代码。
R_BAD_CREDENTIAL = "bad_credential"   # 密码错
R_TOKEN_EXPIRED = "token_expired"     # 令牌过期
R_ACCOUNT_LOCKED = "account_locked"   # 账号被锁
R_MFA_FAILED = "mfa_failed"           # 多因素未通过
R_MFA_BYPASSED = "mfa_bypassed"       # 多因素被绕过
R_SESSION_INVALID = "session_invalid" # 会话失效
R_UNKNOWN = "unknown"                 # 未识别的兜底原因

# 每个环节失败时的默认原因（事件只给了 ok=False 而没有 reason 时使用）。
DEFAULT_REASON = {
    STAGE_AUTH: R_BAD_CREDENTIAL,
    STAGE_TOKEN: R_TOKEN_EXPIRED,
    STAGE_MFA: R_MFA_FAILED,
    STAGE_SESSION: R_SESSION_INVALID,
}

# 环节别名 -> 标准环节。
_STAGE_ALIASES = {
    "password": STAGE_AUTH,
    "passwd": STAGE_AUTH,
    "credential": STAGE_AUTH,
    "credentials": STAGE_AUTH,
    "auth": STAGE_AUTH,
    "authentication": STAGE_AUTH,
    "login": STAGE_AUTH,
    "token": STAGE_TOKEN,
    "mfa": STAGE_MFA,
    "2fa": STAGE_MFA,
    "otp": STAGE_MFA,
    "totp": STAGE_MFA,
    "session": STAGE_SESSION,
    "sso": STAGE_SESSION,
}

# 常见原因别名（小写、去空格、去下划线/连字符后匹配）-> 标准原因。
_REASON_ALIASES = {
    "badcredential": R_BAD_CREDENTIAL,
    "badpassword": R_BAD_CREDENTIAL,
    "wrongpassword": R_BAD_CREDENTIAL,
    "passworderror": R_BAD_CREDENTIAL,
    "invalidcredential": R_BAD_CREDENTIAL,
    "invalidcredentials": R_BAD_CREDENTIAL,
    "tokentimeout": R_TOKEN_EXPIRED,
    "expiredtoken": R_TOKEN_EXPIRED,
    "tokenexpired": R_TOKEN_EXPIRED,
    "accountlocked": R_ACCOUNT_LOCKED,
    "locked": R_ACCOUNT_LOCKED,
    "userlocked": R_ACCOUNT_LOCKED,
    "mfafailed": R_MFA_FAILED,
    "mfafailure": R_MFA_FAILED,
    "otpfailed": R_MFA_FAILED,
    "mfabypassed": R_MFA_BYPASSED,
    "mfabypass": R_MFA_BYPASSED,
    "bypass": R_MFA_BYPASSED,
    "sessioninvalid": R_SESSION_INVALID,
    "sessionexpired": R_SESSION_INVALID,
    "invalidsession": R_SESSION_INVALID,
}

# 失败原因 -> 建议的后续动作。
_ACTION_BY_REASON = {
    R_BAD_CREDENTIAL: "retry_with_correct_password",
    R_TOKEN_EXPIRED: "reauthenticate",
    R_ACCOUNT_LOCKED: "contact_admin_to_unlock",
    R_MFA_FAILED: "retry_mfa",
    R_MFA_BYPASSED: "enforce_mfa_and_alert_security",
    R_SESSION_INVALID: "start_new_session",
    R_UNKNOWN: "inspect_and_retry",
}


def _norm(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def classify_stage(step):
    """把事件的 step 归到标准环节；无法识别时原样小写返回。"""
    if not step:
        return STAGE_AUTH
    key = _norm(step)
    return _STAGE_ALIASES.get(key, str(step).lower())


def classify_reason(stage, reason=None, ok=True):
    """归类失败原因。成功事件返回空字符串；失败事件按显式 reason -> 环节默认 -> 兜底。"""
    if ok:
        return ""
    if reason is not None and str(reason).strip():
        key = _norm(reason)
        if key in _REASON_ALIASES:
            return _REASON_ALIASES[key]
        # 未知的显式原因保留原始文本作为代码（小写、空格转下划线），避免信息丢失。
        return "_".join(str(reason).strip().lower().split())
    return DEFAULT_REASON.get(stage, R_UNKNOWN)


def action_for(reason):
    """失败原因对应的后续动作。"""
    return _ACTION_BY_REASON.get(reason, _ACTION_BY_REASON[R_UNKNOWN])
