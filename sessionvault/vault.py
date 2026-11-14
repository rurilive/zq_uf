"""会话加密落盘：带版本密钥、HMAC校验、可回滚轮换、损坏隔离。

磁盘格式（<account>.json）为信封 JSON：
    {"key_version": N, "nonce": hex, "checksum": hex, "payload": hex}
payload 是明文 JSON 经流式异或（HMAC-SHA256 计数器 keystream）后的密文，
checksum 是 HMAC(key, nonce + payload)，密钥按版本存于 _keys.json。
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

DEFAULT_DIR = "sessions"
KEYRING_FILE = "_keys.json"
QUARANTINE_DIR = "quarantine"
QUARANTINE_INDEX = "index.json"
_NONCE_SIZE = 16
_KEY_SIZE = 32

_keyring_lock = threading.RLock()
_locks_guard = threading.Lock()
_file_locks = {}


class CorruptRecordError(ValueError):
    """记录损坏且已隔离。reason 给出明确原因，quarantine_path 指向隔离文件。"""

    def __init__(self, account, reason, quarantine_path):
        super().__init__("account %r corrupted: %s" % (account, reason))
        self.account = account
        self.reason = reason
        self.quarantine_path = quarantine_path


def _path(account, root=None):
    return os.path.join(root or DEFAULT_DIR, account + ".json")


def _lock_for(path):
    with _locks_guard:
        return _file_locks.setdefault(os.path.abspath(path), threading.Lock())


def _atomic_write_json(path, obj):
    tmp = "%s.tmp-%d" % (path, threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _keyring_path(root):
    return os.path.join(root, KEYRING_FILE)


def _load_keyring(root):
    with _keyring_lock:
        path = _keyring_path(root)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        keyring = {"current": 1, "keys": {"1": secrets.token_hex(_KEY_SIZE)}}
        _atomic_write_json(path, keyring)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return keyring


def _save_keyring(root, keyring):
    with _keyring_lock:
        _atomic_write_json(_keyring_path(root), keyring)


def _keystream(key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def _crypt(key, nonce, data):
    stream = _keystream(key, nonce, len(data))
    return bytes(a ^ b for a, b in zip(data, stream))


def _encrypt(session, key, key_version):
    plaintext = json.dumps(session, ensure_ascii=False).encode("utf-8")
    nonce = secrets.token_bytes(_NONCE_SIZE)
    ciphertext = _crypt(key, nonce, plaintext)
    checksum = hmac.new(key, nonce + ciphertext, hashlib.sha256).hexdigest()
    return {
        "key_version": key_version,
        "nonce": nonce.hex(),
        "checksum": checksum,
        "payload": ciphertext.hex(),
    }


def _decrypt(envelope, keys):
    """解不开就抛 ValueError，消息即损坏原因。"""
    if not isinstance(envelope, dict):
        raise ValueError("envelope is not a JSON object")
    for field in ("key_version", "nonce", "checksum", "payload"):
        if field not in envelope:
            raise ValueError("missing field: " + field)
    version = str(envelope["key_version"])
    if version not in keys:
        raise ValueError("unknown key_version: " + version)
    try:
        key = bytes.fromhex(keys[version])
        nonce = bytes.fromhex(envelope["nonce"])
        ciphertext = bytes.fromhex(envelope["payload"])
    except ValueError:
        raise ValueError("envelope fields are not valid hex")
    expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, envelope["checksum"]):
        raise ValueError("checksum mismatch")
    plaintext = _crypt(key, nonce, ciphertext)
    try:
        return json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("decrypted payload is not valid JSON")


def _quarantine(account, path, raw, reason, root):
    qdir = os.path.join(root, QUARANTINE_DIR)
    os.makedirs(qdir, exist_ok=True)
    qpath = os.path.join(qdir, "%s-%d.json" % (account, time.time_ns()))
    with open(qpath, "w", encoding="utf-8") as handle:
        handle.write(raw)
    if os.path.exists(path):
        os.remove(path)
    index_path = os.path.join(qdir, QUARANTINE_INDEX)
    with _lock_for(index_path):
        index = []
        if os.path.exists(index_path):
            with open(index_path, encoding="utf-8") as handle:
                index = json.load(handle)
        index.append({
            "account": account,
            "reason": reason,
            "file": qpath,
            "quarantined_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        _atomic_write_json(index_path, index)
    return qpath


def save(account, session, root=None):
    """用当前版本密钥加密后原子写盘，返回文件路径。"""
    root = root or DEFAULT_DIR
    os.makedirs(root, exist_ok=True)
    path = _path(account, root)
    with _lock_for(path):
        keyring = _load_keyring(root)
        version = keyring["current"]
        key = bytes.fromhex(keyring["keys"][str(version)])
        _atomic_write_json(path, _encrypt(session, key, version))
    return path


def load(account, root=None):
    """解密返回会话 dict；损坏记录先隔离再抛 CorruptRecordError。"""
    root = root or DEFAULT_DIR
    path = _path(account, root)
    with _lock_for(path):
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
        try:
            envelope = json.loads(raw)
            keyring = _load_keyring(root)
            return _decrypt(envelope, keyring["keys"])
        except ValueError as exc:
            qpath = _quarantine(account, path, raw, str(exc), root)
            raise CorruptRecordError(account, str(exc), qpath)


def current_key_version(root=None):
    """当前密钥版本号。"""
    return _load_keyring(root or DEFAULT_DIR)["current"]


def quarantined(root=None):
    """隔离记录列表：account、reason、file、quarantined_at。"""
    index_path = os.path.join(root or DEFAULT_DIR, QUARANTINE_DIR, QUARANTINE_INDEX)
    if not os.path.exists(index_path):
        return []
    with open(index_path, encoding="utf-8") as handle:
        return json.load(handle)


def rotate(root=None, new_key=None):
    """轮换密钥：先用新版本重写全部记录，再切换指针；失败回滚到旧版本。

    返回 {"status": "ok"|"rolled_back", "from_version", "to_version",
          "rotated", "quarantined", "error"}。
    """
    root = root or DEFAULT_DIR
    os.makedirs(root, exist_ok=True)
    with _keyring_lock:
        keyring = _load_keyring(root)
        old_version = keyring["current"]
        new_version = old_version + 1
        new_key_hex = new_key or secrets.token_hex(_KEY_SIZE)
        new_key_bytes = bytes.fromhex(new_key_hex)
        accounts = sorted(
            name[:-5] for name in os.listdir(root)
            if name.endswith(".json") and name != KEYRING_FILE
            and os.path.isfile(os.path.join(root, name))
        )
        staged = []
        backups = {}
        skipped = []
        try:
            for account in accounts:
                final_path = _path(account, root)
                with open(final_path, "rb") as handle:
                    original = handle.read()
                try:
                    session = _decrypt(json.loads(original.decode("utf-8")), keyring["keys"])
                except (ValueError, UnicodeDecodeError) as exc:
                    _quarantine(account, final_path,
                                original.decode("utf-8", errors="replace"), str(exc), root)
                    skipped.append(account)
                    continue
                tmp_path = final_path + ".rotate-tmp"
                with open(tmp_path, "w", encoding="utf-8") as handle:
                    json.dump(_encrypt(session, new_key_bytes, new_version), handle,
                              ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                backups[final_path] = original
                staged.append((account, tmp_path, final_path))
            for account, tmp_path, final_path in staged:
                os.replace(tmp_path, final_path)
            keyring["keys"][str(new_version)] = new_key_hex
            keyring["current"] = new_version
            _save_keyring(root, keyring)
            return {
                "status": "ok",
                "from_version": old_version,
                "to_version": new_version,
                "rotated": [account for account, _, _ in staged],
                "quarantined": skipped,
                "error": None,
            }
        except Exception as exc:
            for _, tmp_path, _ in staged:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass
            for final_path, original in backups.items():
                try:
                    with open(final_path, "wb") as handle:
                        handle.write(original)
                except OSError:
                    pass
            return {
                "status": "rolled_back",
                "from_version": old_version,
                "to_version": old_version,
                "rotated": [],
                "quarantined": skipped,
                "error": str(exc),
            }
