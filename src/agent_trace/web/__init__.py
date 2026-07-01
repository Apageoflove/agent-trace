# src/agent_trace/web/__init__.py
"""Web 可视化模块

M6: FastAPI 后端 + d3-flame-graph + cytoscape.js + WebSocket
"""

from agent_trace.web.app import create_app

__all__ = ["create_app"]
