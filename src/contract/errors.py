"""Validation failures that name where in the artifact they happened."""

from __future__ import annotations


class ContractError(ValueError):
    """An artifact is malformed.

    Carries the path to the offending field, because a capability is meant to
    be reviewed by a human and "steps[3].target.candidates[0]: role_and_name
    needs a role" is a reviewable message where "KeyError: role" is not.
    """

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}" if path else message)


def require(condition: bool, path: str, message: str) -> None:
    if not condition:
        raise ContractError(path, message)


def require_key(data: dict, key: str, path: str):
    if key not in data:
        raise ContractError(path, f"missing required field {key!r}")
    return data[key]


def require_str(data: dict, key: str, path: str, *, allow_empty: bool = False) -> str:
    value = require_key(data, key, path)
    if not isinstance(value, str):
        raise ContractError(f"{path}.{key}", f"expected a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ContractError(f"{path}.{key}", "must not be empty")
    return value


def require_one_of(value: str, allowed: frozenset[str] | set[str], path: str) -> str:
    if value not in allowed:
        raise ContractError(path, f"{value!r} is not one of {sorted(allowed)}")
    return value
