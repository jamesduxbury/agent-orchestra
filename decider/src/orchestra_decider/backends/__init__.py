from ..config import ConfigError
from .base import DecisionBackend
from .static import StaticBackend
from .systemone_http import SystemOneBackend

BACKENDS: dict[str, type] = {
    "static": StaticBackend,
    "systemone": SystemOneBackend,
}


def create_backend(name: str, options: dict) -> DecisionBackend:
    if name not in BACKENDS:
        raise ConfigError(f"unknown backend {name!r}, available: {', '.join(sorted(BACKENDS))}")
    return BACKENDS[name].from_options(options)
