#!/usr/bin/env python3
"""Shared helpers for persistent Agent memory in external governance storage."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Match

try:  # pragma: no cover - platform-specific imports are exercised on their OS.
    import fcntl as _fcntl
except ImportError:  # Windows
    _fcntl = None
try:  # pragma: no cover - platform-specific imports are exercised on their OS.
    import msvcrt as _msvcrt
except ImportError:  # macOS/Linux
    _msvcrt = None

from protocol_lib import ProtocolError, atomic_write, now_iso
from governance_paths import discover_governance_project, resolve_governance_project

LEGACY_BUS_DIR = ".multi-agent-collaboration"
AGENT_ID_RE = re.compile(r"^A\d{2}-[a-z0-9][a-z0-9-]*$")
CHECKPOINT_RE = re.compile(r"^CP-(\d{4})$")
_REDACTED = "[REDACTED]"
_LABELS = r"api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password|passwd|pwd|cookie|secret"


def _keep_prefix(match: Match[str]) -> str:
    return f"{match.group(1)}{_REDACTED}"


def _redact_database_uri(match: Match[str]) -> str:
    return f"{match.group(1)}{_REDACTED}{match.group(3)}"


SecretPattern = tuple[str, re.Pattern[str], Callable[[Match[str]], str]]
SECRET_PATTERNS: list[SecretPattern] = [
    ("authorization_bearer", re.compile(r"(?im)^(\s*Authorization\s*:\s*Bearer\s+)(?!\[REDACTED\])[^\s]+"), _keep_prefix),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), lambda _m: _REDACTED),
    ("database_uri", re.compile(r"(?i)\b((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|mssql)://[^\s:/@]+:)(?!\[REDACTED\])([^\s@]+)(@)"), _redact_database_uri),
    ("url_query_secret", re.compile(rf"(?i)([?&](?:{_LABELS}|auth|authorization|client_secret|access_key|signature)=)(?!\[REDACTED\]|%5BREDACTED%5D)[^&#\s]+"), _keep_prefix),
    ("multiline_cookie", re.compile(r"(?im)^(\s*(?:set-)?cookie\s*:\s*)(?!\[REDACTED\])[^\r\n]*(?:\r?\n[ \t]+[^\r\n]+)+"), _keep_prefix),
    ("cookie", re.compile(r"(?im)^(\s*(?:set-)?cookie\s*:\s*)(?!\[REDACTED\])[^\r\n]+"), _keep_prefix),
    ("password", re.compile(r"(?im)^(\s*(?:password|passwd|pwd)\s*[:=])(?!\s*\[REDACTED\])\s*[^\r\n]+"), _keep_prefix),
    ("aws_secret", re.compile(r"(?im)(\baws[_ -]?secret(?:[_ -]?access)?[_ -]?key\s*[:=])(?!\s*\[REDACTED\])\s*[A-Za-z0-9/+=]{32,}"), _keep_prefix),
    ("labelled_secret", re.compile(rf"(?i)(\b(?:{_LABELS})\s*[:=])(?!\s*\[REDACTED\])\s*[^\s,;]+"), _keep_prefix),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), lambda _m: _REDACTED),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), lambda _m: _REDACTED),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), lambda _m: _REDACTED),
    ("google_oauth", re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}\b"), lambda _m: _REDACTED),
    ("slack_token", re.compile(r"\bxox(?:a|b|p|r|s)-[0-9A-Za-z-]{20,}\b"), lambda _m: _REDACTED),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), lambda _m: _REDACTED),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), lambda _m: _REDACTED),
]


def project_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ProtocolError(f"project root does not exist: {root}")
    return root


def bus_root(
    root: Path,
    *,
    governance_root: str | Path | None = None,
    project_id: str | None = None,
    allow_legacy: bool = False,
) -> Path:
    if project_id:
        return resolve_governance_project(
            root,
            project_id,
            governance_root,
            require_existing=True,
        ).project_dir
    if governance_root is not None:
        return discover_governance_project(root, governance_root).project_dir
    external_error: ProtocolError | None = None
    try:
        return discover_governance_project(root).project_dir
    except ProtocolError as exc:
        external_error = exc
    if not allow_legacy:
        raise external_error
    # Explicit read-only compatibility for Protocol v3 project-local stores.
    bus = root / LEGACY_BUS_DIR
    if not bus.is_dir():
        raise external_error or ProtocolError("external governance binding was not found")
    return bus.resolve()


def agent_root(
    root: Path,
    agent_id: str,
    *,
    governance_root: str | Path | None = None,
    project_id: str | None = None,
) -> Path:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise ProtocolError(f"invalid agent id: {agent_id}")
    agent = bus_root(
        root,
        governance_root=governance_root,
        project_id=project_id,
    ) / "agents" / agent_id
    if not agent.is_dir():
        raise ProtocolError(f"agent does not exist: {agent_id}")
    return agent


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def exclusive_lock(path: Path):
    """Serialize read/modify/write operations on macOS, Linux, and Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fallback: str | None = None
    fallback_descriptor: int | None = None
    try:
        if _fcntl is not None:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX)
        elif _msvcrt is not None:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            _msvcrt.locking(descriptor, _msvcrt.LK_LOCK, 1)
        else:  # Defensive stdlib-only fallback for uncommon Python platforms.
            fallback = str(path) + ".exclusive"
            while True:
                try:
                    fallback_descriptor = os.open(fallback, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                    break
                except FileExistsError:
                    time.sleep(0.05)
        yield
    finally:
        if _fcntl is not None:
            _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        elif _msvcrt is not None:
            os.lseek(descriptor, 0, os.SEEK_SET)
            _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)
        else:
            if fallback_descriptor is not None:
                os.close(fallback_descriptor)
            if fallback is not None:
                os.unlink(fallback)
        os.close(descriptor)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot parse JSON {path}: {exc}") from exc


def redact(text: str) -> tuple[str, int]:
    count = 0
    for _name, pattern, replacement in SECRET_PATTERNS:
        text, replaced = pattern.subn(replacement, text)
        count += replaced
    return text, count


def contains_secret(text: str) -> bool:
    return bool(find_high_confidence_secrets(text))


def find_high_confidence_secrets(text: str) -> list[str]:
    """Return credential classes found, regardless of a caller's redaction policy."""
    return [name for name, pattern, _replacement in SECRET_PATTERNS if pattern.search(text)]


def ensure_no_high_confidence_secrets(text: str) -> None:
    """Fail closed when high-confidence credentials remain in text."""
    findings = find_high_confidence_secrets(text)
    if findings:
        raise ProtocolError(f"high-confidence secret content detected: {', '.join(findings)}")


def body_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_archive_body(text: str) -> str:
    """Extract and canonically normalize archive body text for stable hashing."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("---\n"):
        end = normalized.find("\n---\n", 4)
        if end < 0:
            raise ProtocolError("unterminated archive frontmatter")
        normalized = normalized[end + len("\n---\n"):]
    normalized = normalized.lstrip("\n")
    heading = "# 完整对话归档\n"
    if normalized.startswith(heading):
        normalized = normalized[len(heading):].lstrip("\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def archive_body_sha256(text: str) -> str:
    return body_sha256(normalize_archive_body(text))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Explicit aliases make the helpers discoverable without changing body_sha256's API.
normalized_archive_body_sha256 = archive_body_sha256
sha256_file = file_sha256


def parse_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ProtocolError(f"unterminated frontmatter: {path}")
    result: dict[str, Any] = {}
    for raw in text[4:end].splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        value = value.strip()
        if value in {"null", "~"}:
            parsed: Any = None
        elif value.startswith(("[", "{")):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = value
        elif len(value) >= 2 and value[0] == value[-1] == '"':
            parsed = json.loads(value)
        else:
            parsed = value
        result[key.strip()] = parsed
    return result


def next_immutable_path(directory: Path, stem: str, suffix: str = ".md") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = directory / f"{stem}-{index:02d}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def next_checkpoint_id(directory: Path) -> str:
    numbers = []
    if directory.exists():
        for path in directory.glob("CP-*.md"):
            match = CHECKPOINT_RE.fullmatch(path.stem)
            if match:
                numbers.append(int(match.group(1)))
    return f"CP-{(max(numbers, default=0) + 1):04d}"


def relative_to_bus(path: Path, bus: Path) -> str:
    try:
        return path.resolve().relative_to(bus.resolve()).as_posix()
    except ValueError as exc:
        raise ProtocolError(f"path is outside persistent agent store: {path}") from exc


def ensure_no_secret_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r"(?i)(password|secret|token|cookie|api[_-]?key|private[_-]?key)", str(key)):
                raise ProtocolError(f"secret-like field is forbidden at {path}.{key}")
            ensure_no_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            ensure_no_secret_fields(child, f"{path}[{index}]")


def update_session_sync(agent: Path, archive: Path, last_message_id: int | None) -> None:
    mapping_path = agent / "conversations" / "SESSION_MAP.json"
    mapping = read_json(mapping_path)
    active = mapping.get("active")
    if active:
        active["last_synced_at"] = now_iso()
        active["last_archive"] = archive.relative_to(agent / "conversations").as_posix()
        if last_message_id is not None:
            active["last_synced_message_id"] = last_message_id
    write_json(mapping_path, mapping)
