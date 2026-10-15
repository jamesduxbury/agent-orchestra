from ..config import ConfigError
from .base import DecisionBackend
from .static import StaticBackend

BACKENDS: dict[str, type] = {
    "static": StaticBackend,
}


def create_backend(name: str, options: dict) -> DecisionBackend:
    if name not in BACKENDS:
        raise ConfigError(f"unknown backend {name!r}, available: {', '.join(sorted(BACKENDS))}")
    return BACKENDS[name].from_options(options)
