"""Entry point: python3 -m pv (imports the route modules before serving)."""
from . import inspections, server

inspections.start_worker()
server.serve()
