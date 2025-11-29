# app.py  (FULL FILE – drop-in)
import os, sys
import json
import base64
import asyncio
import threading
import webbrowser
from typing import List, Dict, Tuple, Optional
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

APP_NAME = "CryptoWatcher"

def _base_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

def _user_data_dir():
    # default if CW_DATA_DIR is not set (dev/local)
    home = os.path.expanduser("~")
    d = os.path.join(home, "Library", "Application Support", APP_NAME) if sys.platform=="darwin" \
        else os.path.join(home, f".{APP_NAME.lower()}")
    os.makedirs(d, exist_ok=True)
    return d

BASE_DIR = _base_dir()
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_ROOT = os.getenv("CW_DATA_DIR", _user_data_dir())   # <- override on Railway
os.makedirs(DATA_ROOT, exist_ok=True)
DATA_FILE = os.path.join(DATA_ROOT, "wallets.json")
FAVICON_FILE = os.path.join(STATIC_DIR, "favicon1.png")


BTC_SATOSHIS = 100_000_000
ETH_WEI = 10**18
TRX_SUN = 1_000_000
DECIMALS_6 = 1_000_000

# Canonical chain codes we store
CANONICAL_CHAINS = {"BTC", "ETH", "TRX", "USDT_TRX", "USDT_ETH", "USDC_ETH"}

# ERC20 contracts (mainnet)
ERC20_USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"  # 6 decimals
ERC20_USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # 6 decimals
TRC20_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"          # 6 decimals

HTTP_TIMEOUT = 14.0

FAVICON_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)

wallets_lock = asyncio.Lock()

def ensure_files() -> None:
    os.makedirs(STATIC_DIR, exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
    if not os.path.exists(FAVICON_FILE):
        try:
            with open(FAVICON_FILE, "wb") as f:
                f.write(base64.b64decode(FAVICON_BASE64))
        except Exception:
            pass

ensure_files()

app = FastAPI(title="Crypto Watcher")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ---------- Models ----------

class WalletCreate(BaseModel):
    chain: str = Field(..., description="BTC|ETH|TRX|USDT_TRX|USDT_ETH|USDC_ETH (aliases allowed)")
    address: str
    label: Optional[str] = None
    notes: Optional[str] = None

class WalletUpdate(BaseModel):
    label: Optional[str] = None
    notes: Optional[str] = None

class BulkImportRequest(BaseModel):
    chain: str
    lines: str

# ---------- Chain normalization & validators ----------

def normalize_chain(chain: str) -> str:
    """WHY: Accept user-friendly aliases and map to canonical codes."""
    c = (chain or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "USDC": "USDC_ETH",
        "ERC20_USDC": "USDC_ETH",
        "USDC_ETH": "USDC_ETH",

        "USDT": "USDT_ETH",           # default USDT to ETH if ambiguous
        "ERC20_USDT": "USDT_ETH",
        "USDT_ETH": "USDT_ETH",

        "TRC20_USDT": "USDT_TRX",
        "USDT_TRX": "USDT_TRX",
    }
    if c in {"BTC", "ETH", "TRX"}:
        return c
    return aliases.get(c, c)

def is_valid_btc_address(addr: str) -> bool:
    return isinstance(addr, str) and 26 <= len(addr) <= 62 and (addr.startswith("1") or addr.startswith("3") or addr.startswith("bc1"))

def is_valid_eth_address(addr: str) -> bool:
    if not isinstance(addr, str) or len(addr) != 42 or not addr.startswith("0x"):
        return False
    try:
        int(addr[2:], 16)
        return True
    except ValueError:
        return False

def is_valid_trx_address(addr: str) -> bool:
    return isinstance(addr, str) and addr.startswith("T") and 26 <= len(addr) <= 36

def validate_address(chain: str, address: str) -> None:
    c = normalize_chain(chain)
    ok = (
        is_valid_btc_address(address) if c == "BTC"
        else is_valid_eth_address(address) if c in {"ETH", "USDT_ETH", "USDC_ETH"}
        else is_valid_trx_address(address) if c in {"TRX", "USDT_TRX"}
        else False
    )
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid {c} address format")

# ---------- HTTP client & test hook ----------

_client: Optional[httpx.AsyncClient] = None

def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "CryptoWatcher/1.2"},
        )
    return _client

def set_http_client_for_tests(client: httpx.AsyncClient) -> None:
    global _client
    _client = client

# ---------- Prices ----------

async def fetch_usd_prices() -> Dict[str, float]:
    # Stablecoins default to 1.0 USD
    base = {"BTC": 0.0, "ETH": 0.0, "TRX": 0.0, "USDT_TRX": 1.0, "USDT_ETH": 1.0, "USDC_ETH": 1.0}
    url = ("https://api.coingecko.com/api/v3/simple/price"
           "?ids=bitcoin,ethereum,tron&vs_currencies=usd")
    try:
        r = await get_client().get(url)
        if r.status_code == 200:
            data = r.json()
            base["BTC"] = float(data.get("bitcoin", {}).get("usd", 0.0) or 0.0)
            base["ETH"] = float(data.get("ethereum", {}).get("usd", 0.0) or 0.0)
            base["TRX"] = float(data.get("tron", {}).get("usd", 0.0) or 0.0)
    except Exception:
        pass
    return base

# ---------- Conversions ----------

def to_coin_balance(chain: str, raw: int) -> float:
    c = normalize_chain(chain)
    if c == "BTC": return raw / BTC_SATOSHIS
    if c == "ETH": return raw / ETH_WEI
    if c == "TRX": return raw / TRX_SUN
    if c in {"USDT_TRX", "USDT_ETH", "USDC_ETH"}: return raw / DECIMALS_6
    return 0.0

def build_wallet_response(wallet: Dict, prices: Dict[str, float]) -> Dict:
    c = normalize_chain(wallet["chain"])
    raw = int(wallet.get("last_raw_balance", 0) or 0)
    coin_balance = to_coin_balance(c, raw)
    usd_price = float(prices.get(c if c in prices else "ETH", 0.0) or 0.0)
    usd_balance = coin_balance * usd_price
    return {
        "id": wallet["id"],
        "chain": c,  # respond with canonical
        "address": wallet["address"],
        "label": wallet.get("label", "") or "",
        "notes": wallet.get("notes", "") or "",
        "last_raw_balance": raw,
        "raw_balance": raw,
        "coin_balance": coin_balance,
        "usd_balance": usd_balance,
    }

def build_wallets_with_balances(wallets: List[Dict], prices: Dict[str, float]) -> Tuple[List[Dict], float]:
    res, total = [], 0.0
    for w in wallets:
        out = build_wallet_response(w, prices)
        total += float(out["usd_balance"])
        res.append(out)
    return res, total

# ---------- Fetch helpers ----------

async def _retry(call, *args, previous: int) -> Tuple[int, bool]:
    rate_limited = False
    for attempt in range(3):
        try:
            raw, rl = await call(*args, previous=previous)
            rate_limited = rate_limited or rl
            return raw, rate_limited
        except Exception:
            await asyncio.sleep(0.25 * (attempt + 1))
    return previous, rate_limited

# --- BTC ---

async def _btc_blockstream(address: str, *, previous: int) -> Tuple[int, bool]:
    r = await get_client().get(f"https://blockstream.info/api/address/{address}")
    if r.status_code == 429: return previous, True
    r.raise_for_status()
    d = r.json()
    cs, ms = d.get("chain_stats", {}) or {}, d.get("mempool_stats", {}) or {}
    funded = int(cs.get("funded_txo_sum", 0)) + int(ms.get("funded_txo_sum", 0))
    spent  = int(cs.get("spent_txo_sum", 0))  + int(ms.get("spent_txo_sum", 0))
    return max(funded - spent, 0), False

async def _btc_blockcypher(address: str, *, previous: int) -> Tuple[int, bool]:
    r = await get_client().get(f"https://api.blockcypher.com/v1/btc/main/addrs/{address}/balance")
    if r.status_code == 429: return previous, True
    r.raise_for_status()
    return int(r.json().get("balance", 0) or 0), False

async def fetch_btc_raw_balance(address: str, previous: int) -> Tuple[int, bool]:
    try:
        return await _retry(_btc_blockstream, address, previous=previous)
    except Exception:
        return await _retry(_btc_blockcypher, address, previous=previous)

# --- ETH native (beefed up) ---

ETH_RPCS = [
    "https://cloudflare-eth.com",
    "https://rpc.ankr.com/eth",
    "https://ethereum.publicnode.com",
]

async def _eth_rpc_get_balance(rpc: str, address: str, *, previous: int) -> Tuple[int, bool]:
    payload = {"jsonrpc": "2.0", "method": "eth_getBalance", "params": [address, "latest"], "id": 1}
    r = await get_client().post(rpc, json=payload)
    if r.status_code == 429: return previous, True
    r.raise_for_status()
    result = (r.json() or {}).get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        return previous, False
    return int(result, 16), False

async def _etherscan_balance(address: str, *, previous: int) -> Tuple[int, bool]:
    # Public no-key endpoint (rate limited but works as a final fallback)
    url = f"https://api.etherscan.io/api?module=account&action=balance&address={address}&tag=latest"
    r = await get_client().get(url)
    if r.status_code == 429: return previous, True
    r.raise_for_status()
    data = r.json()
    if str(data.get("status")) == "1":
        return int(data.get("result") or "0"), False
    return previous, False

async def fetch_eth_raw_balance(address: str, previous: int) -> Tuple[int, bool]:
    last_exc = None
    rate_limited = False
    for rpc in ETH_RPCS:
        try:
            raw, rl = await _eth_rpc_get_balance(rpc, address, previous=previous)
            rate_limited = rate_limited or rl
            if raw != previous or rl:
                return raw, rate_limited
        except Exception as e:
            last_exc = e
            continue
    try:
        return await _retry(_etherscan_balance, address, previous=previous)
    except Exception:
        if last_exc:
            # WHY: ensure endpoint doesn't silently stay at 0 on persistent failures
            raise last_exc
        raise

# --- TRX native ---

async def _trx_trongrid(address: str, *, previous: int) -> Tuple[int, bool]:
    r = await get_client().get(f"https://api.trongrid.io/v1/accounts/{address}")
    if r.status_code == 429: return previous, True
    r.raise_for_status()
    data = r.json()
    arr = data.get("data") or []
    if not arr: return 0, False
    return int(arr[0].get("balance", 0) or 0), False

async def _trx_tronscan(address: str, *, previous: int) -> Tuple[int, bool]:
    r = await get_client().get("https://apilist.tronscanapi.com/api/accountv2", params={"address": address})
    if r.status_code == 429: return previous, True
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        dlist = data.get("data")
        if isinstance(dlist, list) and dlist:
            try:
                return int((dlist[0] or {}).get("balance", 0) or 0), False
            except Exception:
                pass
    return 0, False

async def fetch_trx_raw_balance(address: str, previous: int) -> Tuple[int, bool]:
    try:
        return await _retry(_trx_trongrid, address, previous=previous)
    except Exception:
        return await _retry(_trx_tronscan, address, previous=previous)

# --- ERC20 balances (USDT/USDC) ---

def _erc20_balanceof_data(addr: str) -> str:
    # 70a08231 + 12-byte pad + 20-byte address
    return "0x70a08231" + ("0" * 24) + addr[2:].lower()

async def _eth_rpc_call_balance(rpc_url: str, token: str, address: str, *, previous: int) -> Tuple[int, bool]:
    data = _erc20_balanceof_data(address)
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": token, "data": data}, "latest"], "id": 1}
    r = await get_client().post(rpc_url, json=payload)
    if r.status_code == 429: return previous, True
    r.raise_for_status()
    out = (r.json() or {}).get("result")
    if isinstance(out, str) and out.startswith("0x"):
        return int(out, 16), False
    return previous, False

async def fetch_erc20_raw_balance(address: str, token_contract: str, previous: int) -> Tuple[int, bool]:
    rate_limited = False
    for rpc in ETH_RPCS:
        try:
            raw, rl = await _eth_rpc_call_balance(rpc, token_contract, address, previous=previous)
            rate_limited = rate_limited or rl
            if raw != previous or rl:
                return raw, rate_limited
        except Exception:
            continue
    return previous, rate_limited

# --- TRC20 USDT ---

async def _trc20_from_trongrid(address: str, contract: str, *, previous: int) -> Tuple[int, bool]:
    r = await get_client().get(f"https://api.trongrid.io/v1/accounts/{address}")
    if r.status_code == 429: return previous, True
    r.raise_for_status()
    data = r.json()
    arr = data.get("data") or []
    if not arr: return 0, False
    trc20_list = arr[0].get("trc20") or []
    for item in trc20_list:
        if isinstance(item, dict) and contract in item:
            try:
                return int(item[contract]), False
            except Exception:
                pass
    return 0, False

async def _trc20_from_tronscan(address: str, contract: str, *, previous: int) -> Tuple[int, bool]:
    r = await get_client().get("https://apilist.tronscanapi.com/api/accountv2", params={"address": address})
    if r.status_code == 429: return previous, True
    r.raise_for_status()
    data = r.json()
    balances = []
    if isinstance(data, dict):
        if "trc20token_balances" in data:
            balances = data.get("trc20token_balances") or []
        elif isinstance(data.get("data"), list) and data["data"]:
            balances = (data["data"][0] or {}).get("trc20token_balances") or []
    for it in balances:
        if (it or {}).get("contract_address") == contract:
            try:
                return int(it.get("balance") or 0), False
            except Exception:
                pass
    return 0, False

async def fetch_trc20_raw_balance(address: str, token_contract: str, previous: int) -> Tuple[int, bool]:
    try:
        return await _retry(_trc20_from_trongrid, address, token_contract, previous=previous)
    except Exception:
        return await _retry(_trc20_from_tronscan, address, token_contract, previous=previous)

# ---------- Chain selector ----------

async def fetch_chain_raw_balance(chain: str, address: str, previous: int) -> Tuple[int, bool]:
    c = normalize_chain(chain)
    if c == "BTC":
        return await fetch_btc_raw_balance(address, previous)
    if c == "ETH":
        return await fetch_eth_raw_balance(address, previous)
    if c == "TRX":
        return await fetch_trx_raw_balance(address, previous)
    if c == "USDT_TRX":
        return await fetch_trc20_raw_balance(address, TRC20_USDT, previous)
    if c == "USDT_ETH":
        return await fetch_erc20_raw_balance(address, ERC20_USDT, previous)
    if c == "USDC_ETH":
        return await fetch_erc20_raw_balance(address, ERC20_USDC, previous)
    return previous, False

# ---------- Storage ----------

async def load_wallets() -> List[Dict]:
    async with wallets_lock:
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []
        out = []
        for w in data if isinstance(data, list) else []:
            if not isinstance(w, dict): continue
            wid = int(w.get("id", 0) or 0)
            chain = normalize_chain(w.get("chain", ""))
            addr = str(w.get("address", "")).strip()
            if not wid or chain not in CANONICAL_CHAINS or not addr:
                continue
            out.append({
                "id": wid,
                "chain": chain,
                "address": addr,
                "label": w.get("label", "") or "",
                "notes": w.get("notes", "") or "",
                "last_raw_balance": int(w.get("last_raw_balance", 0) or 0),
            })
        return out

async def save_wallets(wallets: List[Dict]) -> None:
    async with wallets_lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(wallets, f, ensure_ascii=False, indent=2)

def next_wallet_id(wallets: List[Dict]) -> int:
    return (max((w.get("id", 0) for w in wallets), default=0) or 0) + 1

# ---------- Routes ----------

@app.get("/")
async def index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="index.html not found in /static")
    return FileResponse(index_path)

@app.get("/favicon.ico")
async def favicon():
    return RedirectResponse(url="/static/favicon1.png")

@app.get("/api/wallets")
async def get_wallets():
    wallets = await load_wallets()
    prices = await fetch_usd_prices()
    wallets_with_balances, _total = build_wallets_with_balances(wallets, prices)
    return wallets_with_balances

@app.post("/api/wallets")
async def create_wallet(payload: WalletCreate):
    chain = normalize_chain(payload.chain)
    if chain not in CANONICAL_CHAINS:
        raise HTTPException(status_code=400, detail=f"Unsupported chain: {payload.chain}")
    address = (payload.address or "").strip()
    if not address:
        raise HTTPException(status_code=400, detail="Address is required")
    validate_address(chain, address)

    wallets = await load_wallets()
    wid = next_wallet_id(wallets)
    wallet = {
        "id": wid,
        "chain": chain,  # store canonical code
        "address": address,
        "label": (payload.label or "").strip(),
        "notes": (payload.notes or "").strip(),
        "last_raw_balance": 0,
    }
    wallets.append(wallet)
    await save_wallets(wallets)
    return wallet

@app.post("/api/wallets/bulk")
async def bulk_create_wallets(payload: BulkImportRequest):
    chain = normalize_chain(payload.chain)
    if chain not in CANONICAL_CHAINS:
        raise HTTPException(status_code=400, detail=f"Unsupported chain: {payload.chain}")
    wallets = await load_wallets()
    created = []
    for line in (payload.lines or "").splitlines():
        line = (line or "").strip()
        if not line:
            continue
        if "," in line:
            addr, label = line.split(",", 1)
            addr, label = addr.strip(), label.strip()
        else:
            addr, label = line, ""
        try:
            validate_address(chain, addr)
        except HTTPException:
            continue
        wid = next_wallet_id(wallets)
        wallet = {"id": wid, "chain": chain, "address": addr, "label": label, "notes": "", "last_raw_balance": 0}
        wallets.append(wallet); created.append(wallet)
    await save_wallets(wallets)
    return created

@app.put("/api/wallets/{wallet_id}")
async def update_wallet(wallet_id: int, payload: WalletUpdate):
    wallets = await load_wallets()
    updated = None
    for w in wallets:
        if w.get("id") == wallet_id:
            if payload.label is not None: w["label"] = (payload.label or "").strip()
            if payload.notes is not None: w["notes"] = (payload.notes or "").strip()
            updated = w
            break
    if not updated:
        raise HTTPException(status_code=404, detail="Wallet not found")
    await save_wallets(wallets)
    return updated

@app.delete("/api/wallets/{wallet_id}")
async def delete_wallet(wallet_id: int):
    wallets = await load_wallets()
    new_wallets = [w for w in wallets if w.get("id") != wallet_id]
    if len(new_wallets) == len(wallets):
        raise HTTPException(status_code=404, detail="Wallet not found")
    await save_wallets(new_wallets)
    return {"status": "ok"}

@app.delete("/api/wallets")
async def delete_all_wallets():
    await save_wallets([])
    return {"status": "ok"}

@app.post("/api/check")
async def check_wallets():
    wallets = await load_wallets()
    chain_rate_limited: Dict[str, bool] = {c: False for c in CANONICAL_CHAINS}

    async def update_wallet_balance(w: Dict) -> Optional[int]:
        old_raw = int(w.get("last_raw_balance", 0) or 0)
        new_raw, rl = await fetch_chain_raw_balance(w["chain"], w["address"], old_raw)
        if rl: chain_rate_limited[w["chain"]] = True
        w["last_raw_balance"] = int(new_raw)
        return w["id"] if new_raw > old_raw else None

    deposits = await asyncio.gather(*(update_wallet_balance(w) for w in wallets))
    deposits = [d for d in deposits if d is not None]
    await save_wallets(wallets)

    prices = await fetch_usd_prices()
    wallets_with_balances, total_usd = build_wallets_with_balances(wallets, prices)

    chain_status = {
        c: {
            "status": "cooldown" if chain_rate_limited.get(c) else "ok",
            "cooldown_remaining": 8 if chain_rate_limited.get(c) else 0,
        } for c in CANONICAL_CHAINS
    }

    return {
        "wallets": wallets_with_balances,
        "total_usd": total_usd,
        "usd_prices": prices,
        "deposits": deposits,
        "chain_status": chain_status,
    }

def open_browser():
    try:
        webbrowser.open("http://localhost:8000")
    except Exception:
        pass

if __name__ == "__main__":
    timer = threading.Timer(1.0, open_browser)
    timer.daemon = True
    timer.start()
    uvicorn.run(app, host="0.0.0.0", port=8000)

