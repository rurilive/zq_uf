"""可重入的多步表单状态机（纯标准库）。"""

import hashlib
import json
import time

_DEFAULT_CAPTCHA_TIMEOUT = 300


def _fingerprint(page):
    return hashlib.sha256(page.encode("utf-8")).hexdigest()[:16]


def _idem_key(form_name, values):
    payload = json.dumps(values, sort_keys=True, ensure_ascii=False)
    raw = "%s|%s" % (form_name, payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_form(form):
    if isinstance(form, dict):
        return {"name": str(form.get("name", "form")),
                "steps": list(form.get("steps", []))}
    return {"name": str(form), "steps": []}


def _step_spec(state, index):
    steps = state["spec"]["steps"]
    if index < len(steps):
        return steps[index]
    return {"name": "step-%d" % index}


def _page_of(state, index, spec):
    return spec.get("page", "auto:%s:%d" % (state["spec"]["name"], index))


def _trail(state, step_name, fingerprint, idem_key, status, **extra):
    entry = {
        "seq": len(state["trail"]),
        "step": step_name,
        "fingerprint": fingerprint,
        "idem_key": idem_key,
        "status": status,
    }
    entry.update(extra)
    state["trail"].append(entry)
    return entry


def _maybe_done(state):
    steps = state["spec"]["steps"]
    if steps and state["current"] >= len(steps) and state["status"] == "active":
        state["status"] = "done"


def start(form):
    """创建表单状态。form 为名字或 {"name": ..., "steps": [...]} 规格。"""
    spec = _normalize_form(form)
    return {
        "form": spec["name"],
        "spec": spec,
        "step": 0,
        "current": 0,
        "values": {},
        "status": "active",
        "pending": None,
        "results": {},
        "attempts": {},
        "trail": [],
    }


def step(state, values):
    """推进一步；重复提交幂等返回，失败回退一步，验证码挂起等回填。"""
    state["step"] += 1
    state["values"].update(values)

    if state["status"] == "failed":
        _trail(state, None, None, None, "ignored", reason="already_failed")
        return state
    if state["status"] == "done":
        _trail(state, None, None, None, "ignored", reason="already_done")
        return state
    if state["status"] == "suspended":
        return _resume_captcha(state, values)
    return _submit(state, values)


def _submit(state, values):
    index = state["current"]
    spec = _step_spec(state, index)
    name = spec.get("name", "step-%d" % index)
    fingerprint = _fingerprint(_page_of(state, index, spec))

    key = _idem_key(state["form"], values)
    hit = state["results"].get(key)
    if hit is not None:
        state["current"] = max(state["current"], hit["index"] + 1)
        _trail(state, hit["step"], hit["fingerprint"], key, "idempotent", hit=True)
        _maybe_done(state)
        return state

    field = spec.get("field", "captcha")
    if spec.get("type") == "captcha" and field not in values:
        state["status"] = "suspended"
        state["pending"] = {
            "step": name,
            "index": index,
            "field": field,
            "fingerprint": fingerprint,
            "suspended_at": time.time(),
            "timeout": spec.get("timeout", _DEFAULT_CAPTCHA_TIMEOUT),
        }
        _trail(state, name, fingerprint, None, "suspended")
        return state

    fail_times = spec.get("fail_times", 1 if spec.get("fail") else 0)
    attempt = state["attempts"].get(str(index), 0)
    if attempt < fail_times:
        state["attempts"][str(index)] = attempt + 1
        state["current"] = max(0, index - 1)
        _trail(state, name, fingerprint, key, "failed",
               fallback_reason=spec.get("fail_reason", "submit_failed"),
               fallback_to=state["current"])
        return state

    state["results"][key] = {
        "key": key,
        "step": name,
        "index": index,
        "fingerprint": fingerprint,
        "values": dict(values),
        "status": "ok",
    }
    state["current"] = index + 1
    _trail(state, name, fingerprint, key, "ok")
    _maybe_done(state)
    return state


def _resume_captcha(state, values):
    pending = state["pending"]
    if time.time() - pending["suspended_at"] > pending["timeout"]:
        state["status"] = "failed"
        state["pending"] = None
        _trail(state, pending["step"], pending["fingerprint"], None, "failed",
               fallback_reason="captcha_timeout")
        return state
    if pending["field"] not in values:
        _trail(state, pending["step"], pending["fingerprint"], None, "waiting")
        return state
    state["status"] = "active"
    state["pending"] = None
    return _submit(state, values)


def dumps(state):
    """序列化状态；重启后 loads 恢复，从断点继续。"""
    return json.dumps(state, ensure_ascii=False, sort_keys=True)


def loads(data):
    return json.loads(data)

