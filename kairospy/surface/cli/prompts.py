from __future__ import annotations

import sys


def prompt_choice(label: str, choices: tuple[str, ...], *, default: str) -> str:
    if not sys.stdin.isatty():
        prompt = f"{label} [{'/'.join(choices)}] ({default}): "
        value = input(prompt).strip()
        return value if value in choices else default
    try:
        import questionary
    except Exception:
        prompt = f"{label} [{'/'.join(choices)}] ({default}): "
        value = input(prompt).strip()
        return value if value in choices else default
    value = questionary.select(label, choices=list(choices), default=default).ask()
    return str(value or default)


def prompt_text(label: str, default: str) -> str:
    if not sys.stdin.isatty():
        value = input(f"{label} ({default}): ").strip()
        return value or default
    try:
        import questionary
    except Exception:
        value = input(f"{label} ({default}): ").strip()
        return value or default
    value = questionary.text(label, default=default).ask()
    return str(value or default)


def prompt_bool(label: str, default: bool) -> bool:
    if not sys.stdin.isatty():
        value = input(f"{label} ({'yes' if default else 'no'}): ").strip().lower()
        if value in {"y", "yes", "true", "1"}:
            return True
        if value in {"n", "no", "false", "0"}:
            return False
        return default
    try:
        import questionary
    except Exception:
        value = input(f"{label} ({'yes' if default else 'no'}): ").strip().lower()
        if value in {"y", "yes", "true", "1"}:
            return True
        if value in {"n", "no", "false", "0"}:
            return False
        return default
    value = questionary.confirm(label, default=default).ask()
    return bool(default if value is None else value)
