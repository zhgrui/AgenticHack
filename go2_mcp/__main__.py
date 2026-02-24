"""Entry point: python -m go2_mcp"""

from .server import mcp

mcp.run(transport="stdio")
