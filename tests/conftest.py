# tests/conftest.py
import asyncio
import json
import pytest
import httpx

from app import set_http_client_for_tests

@pytest.fixture(autouse=True)
def _event_loop_policy():
    # Ensure a clean loop policy on some OS/CI
    yield

@pytest.fixture()
def mock_transport():
    routes = {}

    def add(method, url, json_body=None, status_code=200, text=None, params=None):
        key = (method.upper(), url, tuple(sorted((params or {}).items())))
        routes[key] = (status_code, json_body, text)

    def handler(request: httpx.Request) -> httpx.Response:
        # match by method, url, and sorted params
        try:
            params = tuple(sorted(httpx.QueryParams(str(request.url).split("?",1)[1] if "?" in str(request.url) else "") .items()))
        except Exception:
            params = tuple()
        key = (request.method.upper(), str(request.url).split("?",1)[0], params)
        status, json_body, text = routes.get(key, (404, {"error":"not found"}, None))
        if json_body is not None:
            return httpx.Response(status, json=json_body)
        return httpx.Response(status, text=text or "")

    transport = httpx.MockTransport(handler)
    transport.add = add
    return transport

@pytest.fixture(autouse=True)
async def inject_client(mock_transport):
    client = httpx.AsyncClient(transport=mock_transport, timeout=5.0)
    set_http_client_for_tests(client)
    yield
    await client.aclose()
