# app.py
import asyncio
import base64
import json
import os
import threading
import time
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Path as FPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
WALLETS_FILE = BASE_DIR / "wallets.json"
FAVICON_PATH = STATIC_DIR / "favicon1.png"

SUPPORTED_CHAINS = {"BTC", "ETH", "TRX"}

ETH_USDT_CONTRACT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
ETH_USDC_CONTRACT = "0xA0b86991C6218b36c1d19D4a2e9Eb0cE3606EB48"
TRX_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "").strip()
TRONSCAN_API_KEY = os.getenv("TRONSCAN_API_KEY", "").strip()


def ensure_static_and_wallets():
    STATIC_DIR.mkdir(exist_ok=True)
    if not WALLETS_FILE.exists():
        WALLETS_FILE.write_text("[]", encoding="utf-8")
    # Fallback favicon (1x1 transparent PNG)
    if not FAVICON_PATH.exists():
        tiny_png_base64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAr8B9p/SUXgAAAAASUVORK5CYII="
        )
        FAVICON_PATH.write_bytes(base64.b64decode(tiny_png_base64))


ensure_static_and_wallets()

app = FastAPI(title="Crypto Watcher")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class Wallet(BaseModel):
    id: int
    chain: str
    address: str
    label: str = ""
    notes: str = ""
    last_raw_balance: int = 0

    @validator("chain")
    def validate_chain(cls, v: str) -> str:
        if v not in SUPPORTED_CHAINS:
            raise ValueError("Unsupported chain")
        return v


class TokenBalance(BaseModel):
    symbol: str          # e.g. "USDT", "USDC"
    standard: str        # e.g. "ERC20", "TRC20"
    raw_balance: int
    coin_balance: float
    usd_balance: float


class WalletWithBalances(Wallet):
    raw_balance: int
    coin_balance: float
    usd_balance: float
    tokens: List[TokenBalance] = Field(default_factory=list)


class AddWalletRequest(BaseModel):
    chain: str
    address: str
    label: Optional[str] = ""
    notes: Optional[str] = ""

    @validator("chain")
    def validate_chain(cls, v: str) -> str:
        if v not in SUPPORTED_CHAINS:
            raise ValueError("Unsupported chain")
        return v

    @validator("address")
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Address cannot be empty")
        return v


class BulkImportRequest(BaseModel):
    chain: str
    lines: str

    @validator("chain")
    def validate_chain(cls, v: str) -> str:
        if v not in SUPPORTED_CHAINS:
            raise ValueError("Unsupported chain")
        return v


class UpdateWalletRequest(BaseModel):
    label: Optional[str] = None
    notes: Optional[str] = None


class CheckResponse(BaseModel):
    wallets: List[WalletWithBalances]
    total_usd: float
    usd_prices: Dict[str, float]
    deposits: List[int]
    chain_status: Dict[str, Dict[str, float]]


def load_wallets() -> List[Wallet]:
    try:
        raw = WALLETS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else []
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    wallets: List[Wallet] = []
    for item in data:
        try:
            wallets.append(Wallet(**item))
        except Exception:
            continue
    return wallets


def save_wallets(wallets: List[Wallet]) -> None:
    serializable = [w.dict() for w in wallets]
    WALLETS_FILE.write_text(json.dumps(serializable, indent=2), encoding="utf-8")


async def fetch_usd_prices(client: httpx.AsyncClient) -> Dict[str, float]:
    # Include stablecoins for token balances
    default_prices = {"BTC": 0.0, "ETH": 0.0, "TRX": 0.0, "USDT": 1.0, "USDC": 1.0}
    try:
        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,tron,tether,usd-coin&vs_currencies=usd"
        )
        resp = await client.get(url, timeout=10)
        if resp.status_code != 200:
            return default_prices
        data = resp.json()
        return {
            "BTC": float(data.get("bitcoin", {}).get("usd", 0.0)),
            "ETH": float(data.get("ethereum", {}).get("usd", 0.0)),
            "TRX": float(data.get("tron", {}).get("usd", 0.0)),
            "USDT": float(data.get("tether", {}).get("usd", 1.0)),
            "USDC": float(data.get("usd-coin", {}).get("usd", 1.0)),
        }
    except Exception:
        return default_prices


async def get_raw_balance_for_wallet(
    client: httpx.AsyncClient, wallet: Wallet
) -> Tuple[int, bool]:
    """
    Returns (new_raw_balance, is_429).

    - On HTTP 429: returns (old_balance, True)
    - On any other error: returns (0, False)
    - On success: returns (raw_balance, False)
    """
    chain = wallet.chain
    address = wallet.address
    old = wallet.last_raw_balance

    try:
        if chain == "BTC":
            url = f"https://api.blockcypher.com/v1/btc/main/addrs/{address}/balance"
            resp = await client.get(url, timeout=10)
            if resp.status_code == 429:
                return old, True
            if resp.status_code != 200:
                return 0, False
            data = resp.json()
            raw = int(data.get("final_balance", 0))
            return raw, False

        if chain == "ETH":
            url = f"https://api.blockcypher.com/v1/eth/main/addrs/{address}/balance"
            resp = await client.get(url, timeout=10)
            if resp.status_code == 429:
                return old, True
            if resp.status_code != 200:
                return 0, False
            data = resp.json()
            raw = int(data.get("final_balance", 0))
            return raw, False

        if chain == "TRX":
            url = f"https://apilist.tronscan.org/api/account?address={address}"
            resp = await client.get(url, timeout=10)
            if resp.status_code == 429:
                return old, True
            if resp.status_code != 200:
                return 0, False
            data = resp.json()
            bal = data.get("balance", 0)
            try:
                raw = int(bal)
            except Exception:
                raw = 0
            return raw, False

    except Exception:
        return 0, False

    return old, False


async def fetch_erc20_token_balance(
    client: httpx.AsyncClient, address: str, contract: str
) -> Optional[int]:
    if not ETHERSCAN_API_KEY:
        return None
    try:
        params = {
            "module": "account",
            "action": "tokenbalance",
            "contractaddress": contract,
            "address": address,
            "tag": "latest",
            "apikey": ETHERSCAN_API_KEY,
        }
        resp = await client.get("https://api.etherscan.io/api", params=params, timeout=10)
        if resp.status_code == 429:
            # skip updating token (treat as "no change")
            return None
        if resp.status_code != 200:
            return 0
        data = resp.json()
        result = data.get("result")
        if result is None:
            return 0
        return int(result)
    except Exception:
        return 0


async def fetch_eth_tokens(client: httpx.AsyncClient, address: str) -> Dict[str, int]:
    """
    Returns a dict like {"USDT_ERC20": raw, "USDC_ERC20": raw}
    """
    balances: Dict[str, int] = {}
    usdt_raw = await fetch_erc20_token_balance(client, address, ETH_USDT_CONTRACT)
    if usdt_raw is not None:
        balances["USDT_ERC20"] = usdt_raw
    usdc_raw = await fetch_erc20_token_balance(client, address, ETH_USDC_CONTRACT)
    if usdc_raw is not None:
        balances["USDC_ERC20"] = usdc_raw
    return balances


async def fetch_trc20_usdt_balance(
    client: httpx.AsyncClient, address: str
) -> Optional[int]:
    if not TRONSCAN_API_KEY:
        return None
    try:
        params = {
            "contract_address": TRX_USDT_CONTRACT,
            "holder_address": address,
            "start": 0,
            "limit": 1,
        }
        headers = {"TRON-PRO-API-KEY": TRONSCAN_API_KEY}
        resp = await client.get(
            "https://apilist.tronscanapi.com/api/token_trc20/holders",
            params=params,
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 429:
            return None
        if resp.status_code != 200:
            return 0
        data = resp.json()
        holders = data.get("data") or data.get("trc20_tokens") or []
        if not holders:
            return 0
        raw = (
            holders[0].get("balance")
            or holders[0].get("quantity")
            or holders[0].get("amount")
            or "0"
        )
        return int(raw)
    except Exception:
        return 0


async def fetch_trx_tokens(client: httpx.AsyncClient, address: str) -> Dict[str, int]:
    balances: Dict[str, int] = {}
    usdt_raw = await fetch_trc20_usdt_balance(client, address)
    if usdt_raw is not None:
        balances["USDT_TRC20"] = usdt_raw
    return balances


def build_wallets_with_balances(
    wallets: List[Wallet],
    prices: Dict[str, float],
    token_raws_list: Optional[List[Dict[str, int]]] = None,
) -> List[WalletWithBalances]:
    result: List[WalletWithBalances] = []
    for idx, w in enumerate(wallets):
        raw = w.last_raw_balance
        if w.chain == "BTC":
            coin = raw / 1e8
            native_price = prices.get("BTC", 0.0)
        elif w.chain == "ETH":
            coin = raw / 1e18
            native_price = prices.get("ETH", 0.0)
        elif w.chain == "TRX":
            coin = raw / 1e6
            native_price = prices.get("TRX", 0.0)
        else:
            coin = 0.0
            native_price = 0.0

        native_usd = coin * native_price

        token_list: List[TokenBalance] = []
        token_raws: Dict[str, int] = {}
        if token_raws_list is not None and idx < len(token_raws_list):
            token_raws = token_raws_list[idx] or {}

        # ETH tokens: USDT_ERC20, USDC_ERC20
        if w.chain == "ETH":
            usdt_raw = token_raws.get("USDT_ERC20")
            if usdt_raw is not None:
                usdt_coin = usdt_raw / 1e6
                token_list.append(
                    TokenBalance(
                        symbol="USDT",
                        standard="ERC20",
                        raw_balance=usdt_raw,
                        coin_balance=usdt_coin,
                        usd_balance=usdt_coin * prices.get("USDT", 1.0),
                    )
                )
            usdc_raw = token_raws.get("USDC_ERC20")
            if usdc_raw is not None:
                usdc_coin = usdc_raw / 1e6
                token_list.append(
                    TokenBalance(
                        symbol="USDC",
                        standard="ERC20",
                        raw_balance=usdc_raw,
                        coin_balance=usdc_coin,
                        usd_balance=usdc_coin * prices.get("USDC", 1.0),
                    )
                )

        # TRX tokens: USDT_TRC20
        if w.chain == "TRX":
            usdt_trc_raw = token_raws.get("USDT_TRC20")
            if usdt_trc_raw is not None:
                usdt_coin = usdt_trc_raw / 1e6
                token_list.append(
                    TokenBalance(
                        symbol="USDT",
                        standard="TRC20",
                        raw_balance=usdt_trc_raw,
                        coin_balance=usdt_coin,
                        usd_balance=usdt_coin * prices.get("USDT", 1.0),
                    )
                )

        result.append(
            WalletWithBalances(
                **w.dict(),
                raw_balance=raw,
                coin_balance=coin,
                usd_balance=native_usd,
                tokens=token_list,
            )
        )
    return result


def compute_total_usd(wallets_with_balances: List[WalletWithBalances]) -> float:
    total = 0.0
    for w in wallets_with_balances:
        total += w.usd_balance
        for t in w.tokens:
            total += t.usd_balance
    return float(total)


def auto_open_browser():
    time.sleep(1.2)
    try:
        webbrowser.open("http://localhost:8000", new=2)
    except Exception:
        pass


@app.on_event("startup")
async def on_startup():
    ensure_static_and_wallets()
    threading.Thread(target=auto_open_browser, daemon=True).start()


@app.get("/", include_in_schema=False)
async def root():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index_path))


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse(url="/static/favicon1.png")


@app.get("/api/wallets", response_model=List[WalletWithBalances])
async def get_wallets():
    wallets = load_wallets()
    async with httpx.AsyncClient() as client:
        prices = await fetch_usd_prices(client)

        # fetch tokens for each wallet
        token_tasks = []
        for w in wallets:
            if w.chain == "ETH":
                token_tasks.append(fetch_eth_tokens(client, w.address))
            elif w.chain == "TRX":
                token_tasks.append(fetch_trx_tokens(client, w.address))
            else:
                token_tasks.append(asyncio.sleep(0, result={}))
        token_results = await asyncio.gather(*token_tasks)

    wallets_with_balances = build_wallets_with_balances(wallets, prices, token_results)
    return wallets_with_balances


@app.post("/api/wallets", response_model=Wallet)
async def add_wallet(req: AddWalletRequest):
    wallets = load_wallets()
    new_id = (max((w.id for w in wallets), default=0) + 1) if wallets else 1
    wallet = Wallet(
        id=new_id,
        chain=req.chain,
        address=req.address.strip(),
        label=(req.label or "").strip(),
        notes=(req.notes or "").strip(),
        last_raw_balance=0,
    )
    wallets.append(wallet)
    save_wallets(wallets)
    return wallet


@app.post("/api/wallets/bulk", response_model=List[Wallet])
async def bulk_add_wallets(req: BulkImportRequest):
    wallets = load_wallets()
    next_id = max((w.id for w in wallets), default=0) + 1 if wallets else 1
    lines = [line.strip() for line in req.lines.splitlines()]
    created: List[Wallet] = []
    for line in lines:
        if not line:
            continue
        if "," in line:
            addr, label = line.split(",", 1)
            addr = addr.strip()
            label = label.strip()
        else:
            addr = line.strip()
            label = ""
        if not addr:
            continue
        wallet = Wallet(
            id=next_id,
            chain=req.chain,
            address=addr,
            label=label,
            notes="",
            last_raw_balance=0,
        )
        wallets.append(wallet)
        created.append(wallet)
        next_id += 1
    save_wallets(wallets)
    return created


@app.put("/api/wallets/{wallet_id}", response_model=Wallet)
async def update_wallet(
    wallet_id: int = FPath(..., ge=1), req: UpdateWalletRequest = None
):
    wallets = load_wallets()
    target = None
    for w in wallets:
        if w.id == wallet_id:
            target = w
            break
    if not target:
        raise HTTPException(status_code=404, detail="Wallet not found")
    data = target.dict()
    if req.label is not None:
        data["label"] = req.label
    if req.notes is not None:
        data["notes"] = req.notes
    updated = Wallet(**data)
    wallets = [updated if w.id == wallet_id else w for w in wallets]
    save_wallets(wallets)
    return updated


@app.delete("/api/wallets/{wallet_id}")
async def delete_wallet(wallet_id: int = FPath(..., ge=1)):
    wallets = load_wallets()
    wallets = [w for w in wallets if w.id != wallet_id]
    save_wallets(wallets)
    return {"status": "ok"}


@app.delete("/api/wallets")
async def delete_all_wallets():
    save_wallets([])
    return {"status": "ok"}


@app.post("/api/check", response_model=CheckResponse)
async def check_balances():
    wallets = load_wallets()

    async with httpx.AsyncClient() as client:
        # native balances
        native_tasks = [get_raw_balance_for_wallet(client, wallet) for wallet in wallets]
        native_results = await asyncio.gather(*native_tasks)

        # token balances
        token_tasks = []
        for w in wallets:
            if w.chain == "ETH":
                token_tasks.append(fetch_eth_tokens(client, w.address))
            elif w.chain == "TRX":
                token_tasks.append(fetch_trx_tokens(client, w.address))
            else:
                token_tasks.append(asyncio.sleep(0, result={}))
        token_results = await asyncio.gather(*token_tasks)

        prices = await fetch_usd_prices(client)

    if not wallets:
        wallets_with_balances = build_wallets_with_balances(wallets, prices, [])
        return CheckResponse(
            wallets=wallets_with_balances,
            total_usd=0.0,
            usd_prices=prices,
            deposits=[],
            chain_status={
                "BTC": {"status": "ok", "cooldown_remaining": 0},
                "ETH": {"status": "ok", "cooldown_remaining": 0},
                "TRX": {"status": "ok", "cooldown_remaining": 0},
            },
        )

    deposits: List[int] = []
    updated_wallets: List[Wallet] = []
    for wallet, (new_raw, is_429) in zip(wallets, native_results):
        old_raw = wallet.last_raw_balance
        if new_raw > old_raw:
            deposits.append(wallet.id)
        updated_wallets.append(
            Wallet(
                **wallet.dict(exclude={"last_raw_balance"}),
                last_raw_balance=int(new_raw),
            )
        )

    save_wallets(updated_wallets)
    wallets_with_balances = build_wallets_with_balances(
        updated_wallets, prices, token_results
    )
    total_usd = compute_total_usd(wallets_with_balances)

    chain_status = {
        "BTC": {"status": "ok", "cooldown_remaining": 0},
        "ETH": {"status": "ok", "cooldown_remaining": 0},
        "TRX": {"status": "ok", "cooldown_remaining": 0},
    }

    return CheckResponse(
        wallets=wallets_with_balances,
        total_usd=total_usd,
        usd_prices=prices,
        deposits=deposits,
        chain_status=chain_status,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
