"""Entry point: python3 -m farming (imports all route modules before serving)."""
from . import msroutes, server, zones  # noqa: F401  (modules register their routes)

msroutes.start_worker()    # Mavic 3M NDVI previews in the background
server.serve()
