"""Shared test configuration: keep the suite independent of the developer's environment."""

import os
import re

import pytest

_SENSITIVE_ENVIRONMENT = re.compile(r"(?:token|password|secret|api[_-]?key|authorization|cookie)", re.I)


@pytest.fixture(scope="session", autouse=True)
def _scrub_sensitive_environment():
    """The service redactor protects credential-like environment values.

    A developer's or CI runner's own tokens would otherwise be redacted out of
    synthetic fixtures and make assertions depend on the machine. Tests that
    exercise the redactor set their own values explicitly.
    """
    removed = {key: os.environ.pop(key) for key in list(os.environ) if _SENSITIVE_ENVIRONMENT.search(key)}
    try:
        yield
    finally:
        os.environ.update(removed)
