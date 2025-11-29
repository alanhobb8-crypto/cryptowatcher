# tests/test_providers.py
import json
import pytest
import httpx

from app import fetch_chain_raw_balance, set_http_client_for_tests

pytestmark = pytest.mark.asyncio

def _add_blockstream_ok(transport, address, funded=150000, spent=50000, mem_funded=0, mem_spent=0):
    url = f"https://blockstream.info/api/address/{address}"
    body = {"chain_stats":{"funded_txo_sum":funded, "spent_txo_sum":spent},
            "mempool_stats":{"funded_txo_sum":mem_funded,"spent_txo_sum":mem_spent}}
    transport.add("GET", url, json_body=body, status_code=200)

def _add_blockcypher_btc_ok(transport, address, balance=123):
    url = f"https://api.blockcypher.com/v1/btc/main/addrs/{address}/balance"
    transport.add("GET", url, json_body={"balance": balance}, status_code=200)

def _add_cloudflare_eth_ok(transport, address, wei_hex):
    url = "https://cloudflare-eth.com"
    body = {"jsonrpc":"2.0","id":1,"result":wei_hex}
    transport.add("POST", url, json_body=body, status_code=200)

def _add_blockcypher_eth_ok(transport, address, wei_int):
    url = f"https://api.blockcypher.com/v1/eth/main/addrs/{address}/balance"
    transport.add("GET", url, json_body={"balance": wei_int}, status_code=200)

def _add_trongrid_ok(transport, address, sun):
    url = f"https://api.trongrid.io/v1/accounts/{address}"
    transport.add("GET", url, json_body={"data":[{"balance": sun}]}, status_code=200)

def _add_tronscan_ok(transport, address, sun):
    url = "https://apilist.tronscanapi.com/api/accountv2"
    transport.add("GET", url, params={"address": address}, json_body={"data":[{"balance":sun}]}, status_code=200)

def _add_429(transport, method, url, params=None):
    transport.add(method, url, json_body={"error":"rate limit"}, status_code=429, params=params)

async def test_btc_primary_ok(mock_transport):
    addr = "1BitcoinEaterAddressDontSendf59kuE"
    _add_blockstream_ok(mock_transport, addr, funded=200000, spent=50000)  # 150000 sat
    raw, limited = await fetch_chain_raw_balance("BTC", addr, previous=0)
    assert raw == 150000 and limited is False

async def test_btc_fallback_on_error(mock_transport):
    addr = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
    # Primary 404 -> fallback
    mock_transport.add("GET", f"https://blockstream.info/api/address/{addr}", json_body={"err":"x"}, status_code=404)
    _add_blockcypher_btc_ok(mock_transport, addr, balance=321)
    raw, limited = await fetch_chain_raw_balance("BTC", addr, previous=0)
    assert raw == 321 and limited is False

async def test_btc_rate_limited_returns_previous(mock_transport):
    addr = "1RateLimitedxxxxxxxxxxxxxxxxxxxxxx"
    _add_429(mock_transport, "GET", f"https://blockstream.info/api/address/{addr}")
    raw, limited = await fetch_chain_raw_balance("BTC", addr, previous=999)
    assert raw == 999 and limited is True

async def test_eth_primary_ok(mock_transport):
    addr = "0x000000000000000000000000000000000000dead"
    _add_cloudflare_eth_ok(mock_transport, addr, "0x16345785d8a0000")  # 0.1 ETH in wei
    raw, limited = await fetch_chain_raw_balance("ETH", addr, previous=0)
    assert raw == int("0x16345785d8a0000", 16) and limited is False

async def test_eth_fallback_on_error(mock_transport):
    addr = "0x000000000000000000000000000000000000beef"
    mock_transport.add("POST", "https://cloudflare-eth.com", json_body={"jsonrpc":"2.0","error":"x"}, status_code=500)
    _add_blockcypher_eth_ok(mock_transport, addr, wei_int=123456789)
    raw, limited = await fetch_chain_raw_balance("ETH", addr, previous=0)
    assert raw == 123456789 and limited is False

async def test_eth_rate_limited_returns_previous(mock_transport):
    addr = "0x000000000000000000000000000000000000c0de"
    _add_429(mock_transport, "POST", "https://cloudflare-eth.com")
    raw, limited = await fetch_chain_raw_balance("ETH", addr, previous=42)
    assert raw == 42 and limited is True

async def test_trx_primary_ok(mock_transport):
    addr = "TQ5Siy2Pq7p4LK2G3i7peoNwKq6N9GQaeV"
    _add_trongrid_ok(mock_transport, addr, sun=2_000_000)  # 2 TRX
    raw, limited = await fetch_chain_raw_balance("TRX", addr, previous=0)
    assert raw == 2_000_000 and limited is False

async def test_trx_fallback_on_error(mock_transport):
    addr = "TRateLimitedOrMissingxxxxxxxxxxxx"
    mock_transport.add("GET", f"https://api.trongrid.io/v1/accounts/{addr}", json_body={"err": "x"}, status_code=500)
    _add_tronscan_ok(mock_transport, addr, sun=1_500_000)
    raw, limited = await fetch_chain_raw_balance("TRX", addr, previous=0)
    assert raw == 1_500_000 and limited is False

async def test_trx_rate_limited_returns_previous(mock_transport):
    addr = "TRateLimitedxxxxxxxxxxxxxxxxxxxxx"
    _add_429(mock_transport, "GET", f"https://api.trongrid.io/v1/accounts/{addr}")
    raw, limited = await fetch_chain_raw_balance("TRX", addr, previous=777)
    assert raw == 777 and limited is True
