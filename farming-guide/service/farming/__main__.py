"""Entry point: python3 -m farming (imports all route modules before serving)."""
from . import server, zones  # noqa: F401  (zones registers its routes)

server.serve()
