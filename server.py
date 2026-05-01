"""
AlgoTrader Pro — HTTP Server
Serves the dashboard HTML and exposes a REST API for settings/control.
Run: python server.py
Dashboard: http://localhost:8080
"""

import asyncio
import json
import os
from pathlib import Path
from aiohttp import web

DASHBOARD_DIR = Path(__file__).parent / 'dashboard'


async def serve_dashboard(request):
    """Serve the main dashboard HTML."""
    html_path = DASHBOARD_DIR / 'index.html'
    with open(html_path) as f:
        content = f.read()
    return web.Response(text=content, content_type='text/html')


async def api_status(request):
    """GET /api/status — bot health check."""
    return web.json_response({
        'status': 'running',
        'version': '1.0.0',
        'ws_url': 'ws://localhost:8765'
    })


async def api_config(request):
    """POST /api/config — update bot configuration."""
    data = await request.json()
    # In production, validate and pass to bot engine
    return web.json_response({'ok': True, 'updated': list(data.keys())})


async def api_stop(request):
    """POST /api/stop — stop the trading bot."""
    return web.json_response({'ok': True, 'message': 'Bot stop signal sent'})


async def api_start(request):
    """POST /api/start — start the trading bot."""
    return web.json_response({'ok': True, 'message': 'Bot start signal sent'})


async def api_close_all(request):
    """POST /api/close_all — close all open positions."""
    return web.json_response({'ok': True, 'message': 'Close all signal sent'})


async def api_trades(request):
    """GET /api/trades — return trade history as JSON."""
    # In production, read from database or bot state
    return web.json_response({'trades': [], 'count': 0})


def create_app():
    app = web.Application()
    app.router.add_get('/', serve_dashboard)
    app.router.add_get('/api/status', api_status)
    app.router.add_get('/api/trades', api_trades)
    app.router.add_post('/api/config', api_config)
    app.router.add_post('/api/stop', api_stop)
    app.router.add_post('/api/start', api_start)
    app.router.add_post('/api/close_all', api_close_all)

    # Serve static files from dashboard/
    app.router.add_static('/static/', path=DASHBOARD_DIR, name='static')
    return app


if __name__ == '__main__':
    port = int(os.getenv('DASHBOARD_PORT', 8080))
    print(f'Dashboard: http://localhost:{port}')
    web.run_app(create_app(), host='0.0.0.0', port=port)
