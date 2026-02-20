"""
╔══════════════════════════════════════════════════════════════════╗
║       MUSE v2 — Deployment & Client Script                      ║
║       deploy_and_interact.py                                    ║
║                                                                  ║
║  Usage:                                                          ║
║    python deploy_and_interact.py deploy                          ║
║    python deploy_and_interact.py mint --name "Drop #1" --price 10║
║    python deploy_and_interact.py buy --asset 12345678            ║
║    python deploy_and_interact.py auction --asset 12345678        ║
╚══════════════════════════════════════════════════════════════════╝
"""

import argparse
import base64
import hashlib
import json
import os
import time
from typing import Optional

from algosdk import account, encoding, mnemonic
from algosdk.v2client import algod, indexer
from algosdk.transaction import (
    ApplicationCreateTxn,
    ApplicationCallTxn,
    ApplicationNoOpTxn,
    AssetOptInTxn,
    PaymentTxn,
    StateSchema,
    OnComplete,
    wait_for_confirmation,
    assign_group_id,
)
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
    TransactionWithSigner,
)
from beaker.client import ApplicationClient

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────

# Algorand Testnet endpoints (free public nodes)
ALGOD_ADDRESS  = "https://testnet-api.algonode.cloud"
ALGOD_TOKEN    = ""   # AlgoNode doesn't need a token
INDEXER_ADDRESS = "https://testnet-idx.algonode.cloud"
INDEXER_TOKEN   = ""

# Muse treasury address (update before deploying)
MUSE_TREASURY = os.environ.get(
    "MUSE_TREASURY",
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"  # placeholder
)

# App ID after first deploy (update this once you deploy)
APP_ID = int(os.environ.get("MUSE_APP_ID", "0"))


# ─────────────────────────────────────────────
#  CLIENTS
# ─────────────────────────────────────────────
def get_algod() -> algod.AlgodClient:
    return algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)


def get_indexer() -> indexer.IndexerClient:
    return indexer.IndexerClient(INDEXER_TOKEN, INDEXER_ADDRESS)


def load_account(env_var: str = "MUSE_MNEMONIC") -> tuple[str, str]:
    """
    Load an Algorand account from a mnemonic stored in env variable.
    Returns (private_key, address)
    """
    mn = os.environ.get(env_var)
    if not mn:
        # Generate a fresh testnet account for demo purposes
        private_key, address = account.generate_account()
        print(f"\n⚠  No {env_var} set. Generated fresh account:")
        print(f"   Address  : {address}")
        print(f"   Mnemonic : {mnemonic.from_private_key(private_key)}")
        print(f"\n   Fund this address at: https://testnet.algoexplorer.io/dispenser")
        print(f"   Then set: export {env_var}='<your mnemonic>'\n")
        return private_key, address
    private_key = mnemonic.to_private_key(mn)
    address     = account.address_from_private_key(private_key)
    return private_key, address


# ─────────────────────────────────────────────
#  DEPLOY
# ─────────────────────────────────────────────
def deploy(private_key: str, address: str) -> int:
    """Compile and deploy the Muse v2 smart contract to testnet."""
    from contracts.muse_marketplace import app

    client = get_algod()

    print("🔨 Compiling Muse v2 contract...")
    app_spec    = app.build()
    approval_b64 = base64.b64encode(app_spec.approval_program).decode()
    clear_b64    = base64.b64encode(app_spec.clear_program).decode()

    sp = client.suggested_params()

    # Compile
    approval_result = client.compile(base64.b64decode(approval_b64).decode())
    clear_result    = client.compile(base64.b64decode(clear_b64).decode())

    approval_bytes = base64.b64decode(approval_result["result"])
    clear_bytes    = base64.b64decode(clear_result["result"])

    # Global state schema: 5 values
    global_schema = StateSchema(num_uints=4, num_byte_slices=1)
    local_schema  = StateSchema(num_uints=2, num_byte_slices=0)

    # Treasury address as app arg
    treasury_bytes = encoding.decode_address(MUSE_TREASURY)

    txn = ApplicationCreateTxn(
        sender=address,
        sp=sp,
        on_complete=OnComplete.NoOpOC,
        approval_program=approval_bytes,
        clear_program=clear_bytes,
        global_schema=global_schema,
        local_schema=local_schema,
        app_args=[b"create", treasury_bytes],
    )

    signed = txn.sign(private_key)
    tx_id  = client.send_transaction(signed)
    print(f"   Transaction: {tx_id}")

    receipt = wait_for_confirmation(client, tx_id, 4)
    app_id  = receipt["application-index"]

    print(f"✅ Deployed! App ID: {app_id}")
    print(f"   Set: export MUSE_APP_ID={app_id}")
    return app_id


# ─────────────────────────────────────────────
#  MINT SINGLE NFT
# ─────────────────────────────────────────────
def mint_nft(
    private_key: str,
    address: str,
    name: str,
    unit_name: str,
    metadata_url: str,
    metadata_json: dict,
    price_algo: float,
    royalty_pct: float,
    floor_pct: float          = 0.0,
    decay_months: int         = 0,
    buyout_algo: float        = 0.0,
    is_rwa: bool              = False,
    physical_hash: str        = "",
    custodian: str            = "",
    authenticator: str        = "",
) -> int:
    """
    Mint a single ARC-69 NFT through the Muse marketplace contract.

    Args:
        name:           NFT display name
        unit_name:      Short token symbol (max 8 chars)
        metadata_url:   URL to ARC-69 JSON
        metadata_json:  Full ARC-69 metadata dict (will be hashed)
        price_algo:     List price in ALGO (0 = auction only)
        royalty_pct:    Royalty percentage (e.g. 10.0 = 10%)
        floor_pct:      Floor royalty after decay
        decay_months:   Months before full decay to floor (0 = off)
        buyout_algo:    One-time royalty buy-out price in ALGO (0 = off)
        is_rwa:         True to mint as Real World Asset
        physical_hash:  SHA-256 of certificate of authenticity (RWA only)
        custodian:      Vault address holding physical item (RWA only)
        authenticator:  Authenticator address (RWA only)

    Returns:
        Newly created ASA ID
    """
    from contracts.muse_marketplace import app as muse_app

    client     = get_algod()
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, signer=AccountTransactionSigner(private_key))

    # Hash the metadata
    meta_bytes   = json.dumps(metadata_json, sort_keys=True).encode()
    meta_hash    = hashlib.sha256(meta_bytes).digest()  # 32 bytes
    assert len(meta_hash) == 32

    price_micro  = int(price_algo  * 1_000_000)
    buyout_micro = int(buyout_algo * 1_000_000)
    royalty_bps  = int(royalty_pct * 100)
    floor_bps    = int(floor_pct   * 100)
    decay_rounds = decay_months * 777_600  # ~4s per round

    method = "mint_rwa" if is_rwa else "mint_nft"

    base_args = dict(
        name              = name,
        unit_name         = unit_name,
        metadata_url      = metadata_url,
        metadata_hash     = list(meta_hash),
        price_microalgos  = price_micro,
        royalty_bps       = royalty_bps,
        floor_bps         = floor_bps,
        decay_after_rounds= decay_rounds,
        buyout_price      = buyout_micro,
    )

    if is_rwa:
        ph_bytes = bytes.fromhex(physical_hash) if physical_hash else b"\x00" * 32
        base_args.update(dict(
            physical_asset_hash = list(ph_bytes),
            custodian           = custodian or address,
            authenticator       = authenticator or address,
        ))

    print(f"🎨 Minting NFT: '{name}' ({'RWA' if is_rwa else 'Digital'})...")
    result = app_client.call(method, **base_args)
    asset_id = result.return_value

    print(f"✅ Minted! ASA ID: {asset_id}")
    print(f"   View on explorer: https://testnet.algoexplorer.io/asset/{asset_id}")
    return asset_id


# ─────────────────────────────────────────────
#  MINT BATCH
# ─────────────────────────────────────────────
def mint_batch(
    private_key: str,
    address: str,
    items: list[dict],
) -> list[int]:
    """
    Mint multiple NFTs sequentially.

    Args:
        items: List of dicts, each with same fields as mint_nft()

    Returns:
        List of created ASA IDs
    """
    asset_ids = []
    for i, item in enumerate(items):
        print(f"\n📦 Batch item {i+1}/{len(items)}: {item['name']}")
        asset_id = mint_nft(private_key, address, **item)
        asset_ids.append(asset_id)
        time.sleep(0.5)  # small delay between txns

    print(f"\n✅ Batch complete! Minted {len(asset_ids)} NFTs: {asset_ids}")
    return asset_ids


# ─────────────────────────────────────────────
#  REGISTER CO-CREATORS
# ─────────────────────────────────────────────
def register_co_creators(
    private_key: str,
    address: str,
    asset_id: int,
    co_creators: list[dict],
) -> None:
    """
    Register up to 4 co-creators for a royalty split.

    Args:
        asset_id:    The ASA to configure
        co_creators: List of up to 4 dicts with keys:
                     - address (str): Algorand address
                     - share_bps (int): Share of royalty in basis points
                     - role (str): Human-readable role label

    Example:
        co_creators = [
            {"address": "ABC...", "share_bps": 5000, "role": "Athlete"},
            {"address": "DEF...", "share_bps": 2500, "role": "NBA League"},
            {"address": "GHI...", "share_bps": 1500, "role": "Charity"},
            {"address": "JKL...", "share_bps": 1000, "role": "Photographer"},
        ]
    """
    from contracts.muse_marketplace import app as muse_app

    ZERO_ADDR = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"
    client     = get_algod()
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, signer=AccountTransactionSigner(private_key))

    # Pad to 4 entries
    while len(co_creators) < 4:
        co_creators.append({"address": ZERO_ADDR, "share_bps": 0, "role": ""})

    args = dict(
        asset_id    = asset_id,
        addr_1      = co_creators[0]["address"],
        share_bps_1 = co_creators[0]["share_bps"],
        role_1      = co_creators[0]["role"],
        addr_2      = co_creators[1]["address"],
        share_bps_2 = co_creators[1]["share_bps"],
        role_2      = co_creators[1]["role"],
        addr_3      = co_creators[2]["address"],
        share_bps_3 = co_creators[2]["share_bps"],
        role_3      = co_creators[2]["role"],
        addr_4      = co_creators[3]["address"],
        share_bps_4 = co_creators[3]["share_bps"],
        role_4      = co_creators[3]["role"],
    )

    print(f"🤝 Registering co-creators for asset {asset_id}...")
    app_client.call("register_co_creators", **args)
    print("✅ Co-creator registry written to chain. Awaiting acceptances.")


def accept_collaboration(
    private_key: str,
    address: str,
    asset_id: int,
) -> None:
    """
    Called by a co-creator to formally accept their role on-chain.
    """
    from contracts.muse_marketplace import app as muse_app
    client     = get_algod()
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, signer=AccountTransactionSigner(private_key))

    print(f"✅ Accepting collaboration on asset {asset_id}...")
    app_client.call("accept_collaboration", asset_id=asset_id)
    print("   Role accepted on-chain.")


# ─────────────────────────────────────────────
#  BUY NFT
# ─────────────────────────────────────────────
def buy_nft(
    private_key: str,
    address: str,
    asset_id: int,
    price_algo: float,
) -> None:
    """
    Purchase an NFT at fixed price.
    Automatically opts into the ASA if needed.

    Args:
        asset_id:   ASA to purchase
        price_algo: Expected price in ALGO (must match on-chain price)
    """
    from contracts.muse_marketplace import app as muse_app

    client     = get_algod()
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, signer=AccountTransactionSigner(private_key))
    sp         = client.suggested_params()
    price_micro = int(price_algo * 1_000_000)

    # Opt into ASA
    optin_txn = AssetOptInTxn(sender=address, sp=sp, index=asset_id)
    optin_signed = optin_txn.sign(private_key)
    client.send_transaction(optin_signed)
    wait_for_confirmation(client, optin_signed.transaction.get_txid(), 4)
    print(f"   Opted into ASA {asset_id}")

    # Payment transaction (grouped with app call)
    atc = AtomicTransactionComposer()
    payment_txn = PaymentTxn(
        sender=address,
        sp=sp,
        receiver=app_client.app_addr,
        amt=price_micro,
    )
    signer = AccountTransactionSigner(private_key)

    from contracts.muse_marketplace import app as muse_app
    method = muse_app.contract.get_method_by_name("buy_nft")

    atc.add_method_call(
        app_id=APP_ID,
        method=method,
        sender=address,
        sp=sp,
        signer=signer,
        method_args=[
            asset_id,
            TransactionWithSigner(txn=payment_txn, signer=signer),
        ],
    )

    print(f"💰 Buying asset {asset_id} for {price_algo} ALGO...")
    result = atc.execute(client, 4)
    print(f"✅ Purchase complete! Tx: {result.tx_ids[-1]}")


# ─────────────────────────────────────────────
#  ROYALTY BUY-OUT
# ─────────────────────────────────────────────
def buy_out_royalty(
    private_key: str,
    address: str,
    asset_id: int,
    buyout_algo: float,
) -> None:
    """
    Pay to permanently remove royalties from an NFT.

    Args:
        asset_id:     Target ASA
        buyout_algo:  Buy-out price in ALGO
    """
    from contracts.muse_marketplace import app as muse_app
    client      = get_algod()
    sp          = client.suggested_params()
    signer      = AccountTransactionSigner(private_key)
    buyout_micro = int(buyout_algo * 1_000_000)

    atc = AtomicTransactionComposer()
    payment_txn = PaymentTxn(
        sender=address, sp=sp,
        receiver=ApplicationClient(client, muse_app, app_id=APP_ID).app_addr,
        amt=buyout_micro,
    )

    method = muse_app.contract.get_method_by_name("buy_out_royalty")
    atc.add_method_call(
        app_id=APP_ID, method=method,
        sender=address, sp=sp, signer=signer,
        method_args=[
            asset_id,
            TransactionWithSigner(txn=payment_txn, signer=signer),
        ],
    )

    print(f"⊞ Buying out royalties for asset {asset_id} ({buyout_algo} ALGO)...")
    result = atc.execute(client, 4)
    print(f"✅ Royalties permanently waived! Tx: {result.tx_ids[-1]}")


# ─────────────────────────────────────────────
#  AUCTIONS
# ─────────────────────────────────────────────
def start_auction(
    private_key: str,
    address: str,
    asset_id: int,
    duration_hours: float,
    reserve_algo: float,
) -> None:
    """
    Start an English auction for an NFT you own.

    Args:
        asset_id:       ASA to auction
        duration_hours: Auction length in hours
        reserve_algo:   Minimum bid in ALGO
    """
    from contracts.muse_marketplace import app as muse_app
    client     = get_algod()
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, signer=AccountTransactionSigner(private_key))

    duration_rounds = int(duration_hours * 3600 / 4)  # ~4s per round
    reserve_micro   = int(reserve_algo * 1_000_000)

    print(f"⧉ Starting auction for asset {asset_id}...")
    print(f"   Duration: {duration_hours}h ({duration_rounds} rounds)")
    print(f"   Reserve:  {reserve_algo} ALGO")

    app_client.call(
        "start_auction",
        asset_id        = asset_id,
        duration_rounds = duration_rounds,
        reserve_price   = reserve_micro,
    )
    print("✅ Auction started!")


def place_bid(
    private_key: str,
    address: str,
    asset_id: int,
    bid_algo: float,
) -> None:
    """
    Place a bid on an active auction.

    Args:
        asset_id:  ASA being auctioned
        bid_algo:  Bid amount in ALGO (must exceed current highest bid)
    """
    from contracts.muse_marketplace import app as muse_app
    client     = get_algod()
    sp         = client.suggested_params()
    signer     = AccountTransactionSigner(private_key)
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, signer=signer)
    bid_micro  = int(bid_algo * 1_000_000)

    atc = AtomicTransactionComposer()
    payment_txn = PaymentTxn(
        sender=address, sp=sp,
        receiver=app_client.app_addr,
        amt=bid_micro,
    )

    method = muse_app.contract.get_method_by_name("place_bid")
    atc.add_method_call(
        app_id=APP_ID, method=method,
        sender=address, sp=sp, signer=signer,
        method_args=[
            asset_id,
            TransactionWithSigner(txn=payment_txn, signer=signer),
        ],
    )

    print(f"⧉ Placing bid of {bid_algo} ALGO on asset {asset_id}...")
    result = atc.execute(client, 4)
    print(f"✅ Bid placed! Tx: {result.tx_ids[-1]}")


def settle_auction(
    private_key: str,
    address: str,
    asset_id: int,
) -> None:
    """Settle a completed auction (callable by anyone)."""
    from contracts.muse_marketplace import app as muse_app
    client     = get_algod()
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, signer=AccountTransactionSigner(private_key))
    app_client.call("settle_auction", asset_id=asset_id)
    print(f"✅ Auction settled for asset {asset_id}")


# ─────────────────────────────────────────────
#  RWA ACTIONS
# ─────────────────────────────────────────────
def validate_rwa(
    authenticator_key: str,
    authenticator_addr: str,
    asset_id: int,
) -> None:
    """Authenticator validates a physical RWA asset."""
    from contracts.muse_marketplace import app as muse_app
    client     = get_algod()
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, signer=AccountTransactionSigner(authenticator_key))
    app_client.call("validate_physical_asset", asset_id=asset_id)
    print(f"🔐 Asset {asset_id} authenticated! Now active on marketplace.")


def redeem_rwa(
    custodian_key: str,
    custodian_addr: str,
    asset_id: int,
    tracking_info: str,
) -> None:
    """Custodian records physical redemption of an RWA."""
    from contracts.muse_marketplace import app as muse_app
    client     = get_algod()
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, signer=AccountTransactionSigner(custodian_key))
    app_client.call("redeem_physical_asset", asset_id=asset_id, tracking_info=tracking_info)
    print(f"📦 Asset {asset_id} marked as redeemed. Tracking: {tracking_info}")


# ─────────────────────────────────────────────
#  READ-ONLY QUERIES
# ─────────────────────────────────────────────
def query_nft(asset_id: int) -> dict:
    """Fetch and display NFT info from on-chain box storage."""
    from contracts.muse_marketplace import app as muse_app
    client     = get_algod()
    _, address = load_account("MUSE_MNEMONIC")  # any address works for reads
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, sender=address)

    result = app_client.call("get_nft_info", asset_id=asset_id).return_value
    effective_royalty = app_client.call("get_current_royalty", asset_id=asset_id).return_value

    FLAGS = {
        0x01: "IS_RWA",
        0x02: "RWA_AUTHENTICATED",
        0x04: "RWA_REDEEMED",
        0x08: "ROYALTY_WAIVED",
        0x10: "IN_AUCTION",
        0x20: "COLLAB_PENDING",
    }
    flags_val  = result[6]
    flag_names = [name for bit, name in FLAGS.items() if flags_val & bit]

    info = {
        "asset_id":          asset_id,
        "price_algo":        result[0] / 1_000_000,
        "royalty_pct":       result[1] / 100,
        "floor_pct":         result[2] / 100,
        "decay_start_round": result[3],
        "buyout_algo":       result[4] / 1_000_000,
        "transfers":         result[5],
        "flags":             flag_names,
        "creator":           result[7],
        "effective_royalty_pct": effective_royalty / 100,
    }

    print(f"\n{'─'*50}")
    print(f"  NFT INFO — Asset {asset_id}")
    print(f"{'─'*50}")
    for k, v in info.items():
        print(f"  {k:<25} {v}")
    print(f"{'─'*50}\n")
    return info


def query_stats() -> dict:
    """Fetch global marketplace statistics."""
    from contracts.muse_marketplace import app as muse_app
    client     = get_algod()
    _, address = load_account("MUSE_MNEMONIC")
    app_client = ApplicationClient(client, muse_app, app_id=APP_ID, sender=address)

    result = app_client.call("get_platform_stats").return_value
    stats  = {
        "total_volume_algo":    result[0] / 1_000_000,
        "total_royalties_algo": result[1] / 1_000_000,
        "nft_count":            result[2],
        "live_auctions":        result[3],
    }

    print(f"\n{'─'*50}")
    print(f"  MUSE v2 PLATFORM STATS")
    print(f"{'─'*50}")
    for k, v in stats.items():
        print(f"  {k:<30} {v}")
    print(f"{'─'*50}\n")
    return stats


# ─────────────────────────────────────────────
#  CLI ENTRYPOINT
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Muse v2 — NFT + RWA Marketplace CLI for Algorand",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Deploy the contract
  python deploy_and_interact.py deploy

  # Mint a single digital NFT
  python deploy_and_interact.py mint \\
      --name "Solstice Bloom #07" \\
      --unit SLST7 \\
      --url "ipfs://QmABC.../metadata.json" \\
      --price 24 \\
      --royalty 10 \\
      --floor 5 \\
      --decay-months 12 \\
      --buyout 500

  # Mint an RWA token
  python deploy_and_interact.py mint-rwa \\
      --name "LeBron Game-Worn Jersey" \\
      --unit FG7J \\
      --price 4200 \\
      --royalty 12 \\
      --phash abcdef1234... \\
      --custodian ALGO_VAULT_ADDRESS \\
      --authenticator ALGO_AUTH_ADDRESS

  # Register co-creators (JSON)
  python deploy_and_interact.py co-creators \\
      --asset 12345678 \\
      --splits '[{"address":"ABC...","share_bps":5000,"role":"Athlete"},{"address":"DEF...","share_bps":2500,"role":"NBA"},{"address":"GHI...","share_bps":1500,"role":"Charity"},{"address":"JKL...","share_bps":1000,"role":"Media"}]'

  # Buy an NFT
  python deploy_and_interact.py buy --asset 12345678 --price 24

  # Start an auction
  python deploy_and_interact.py auction --asset 12345678 --duration 48 --reserve 100

  # Place a bid
  python deploy_and_interact.py bid --asset 12345678 --amount 150

  # Settle an ended auction
  python deploy_and_interact.py settle --asset 12345678

  # Buy out royalties
  python deploy_and_interact.py buyout --asset 12345678 --amount 500

  # Authenticate an RWA (as authenticator)
  python deploy_and_interact.py auth-rwa --asset 12345678

  # Query NFT info
  python deploy_and_interact.py info --asset 12345678

  # Query platform stats
  python deploy_and_interact.py stats
"""
    )

    sub = parser.add_subparsers(dest="cmd")

    # deploy
    sub.add_parser("deploy", help="Deploy the contract to testnet")

    # mint
    p_mint = sub.add_parser("mint", help="Mint a digital NFT")
    p_mint.add_argument("--name",          required=True)
    p_mint.add_argument("--unit",          default="NFT")
    p_mint.add_argument("--url",           default="ipfs://placeholder/metadata.json")
    p_mint.add_argument("--price",         type=float, default=10.0)
    p_mint.add_argument("--royalty",       type=float, default=10.0)
    p_mint.add_argument("--floor",         type=float, default=0.0)
    p_mint.add_argument("--decay-months",  type=int,   default=0)
    p_mint.add_argument("--buyout",        type=float, default=0.0)

    # mint-rwa
    p_rwa = sub.add_parser("mint-rwa", help="Mint an RWA token")
    p_rwa.add_argument("--name",         required=True)
    p_rwa.add_argument("--unit",         default="RWA")
    p_rwa.add_argument("--url",          default="ipfs://placeholder/rwa.json")
    p_rwa.add_argument("--price",        type=float, default=1000.0)
    p_rwa.add_argument("--royalty",      type=float, default=10.0)
    p_rwa.add_argument("--floor",        type=float, default=5.0)
    p_rwa.add_argument("--decay-months", type=int,   default=0)
    p_rwa.add_argument("--buyout",       type=float, default=0.0)
    p_rwa.add_argument("--phash",        default="0" * 64, help="SHA-256 hex of CoA")
    p_rwa.add_argument("--custodian",    required=True)
    p_rwa.add_argument("--authenticator", required=True)

    # co-creators
    p_co = sub.add_parser("co-creators", help="Register co-creator royalty splits")
    p_co.add_argument("--asset",  type=int, required=True)
    p_co.add_argument("--splits", required=True, help="JSON array of co-creator entries")

    # accept
    p_acc = sub.add_parser("accept", help="Accept a co-creator invitation")
    p_acc.add_argument("--asset", type=int, required=True)

    # buy
    p_buy = sub.add_parser("buy", help="Buy an NFT at fixed price")
    p_buy.add_argument("--asset", type=int, required=True)
    p_buy.add_argument("--price", type=float, required=True)

    # buyout
    p_bo = sub.add_parser("buyout", help="Buy out royalties permanently")
    p_bo.add_argument("--asset",  type=int,   required=True)
    p_bo.add_argument("--amount", type=float, required=True)

    # auction
    p_auc = sub.add_parser("auction", help="Start an auction")
    p_auc.add_argument("--asset",    type=int,   required=True)
    p_auc.add_argument("--duration", type=float, default=48.0, help="Duration in hours")
    p_auc.add_argument("--reserve",  type=float, default=0.0,  help="Reserve price in ALGO")

    # bid
    p_bid = sub.add_parser("bid", help="Place a bid on an auction")
    p_bid.add_argument("--asset",  type=int,   required=True)
    p_bid.add_argument("--amount", type=float, required=True)

    # settle
    p_settle = sub.add_parser("settle", help="Settle a completed auction")
    p_settle.add_argument("--asset", type=int, required=True)

    # auth-rwa
    p_auth = sub.add_parser("auth-rwa", help="Authenticate an RWA (as authenticator)")
    p_auth.add_argument("--asset", type=int, required=True)

    # redeem-rwa
    p_redeem = sub.add_parser("redeem-rwa", help="Redeem physical RWA (as custodian)")
    p_redeem.add_argument("--asset",    type=int,   required=True)
    p_redeem.add_argument("--tracking", type=str,   default="", help="Tracking/collection reference")

    # info
    p_info = sub.add_parser("info", help="Query NFT info from chain")
    p_info.add_argument("--asset", type=int, required=True)

    # stats
    sub.add_parser("stats", help="View platform-wide statistics")

    args = parser.parse_args()

    if not args.cmd:
        parser.print_help()
        return

    private_key, address = load_account()
    print(f"\n👛 Wallet: {address}")
    print(f"   App ID: {APP_ID}\n")

    if args.cmd == "deploy":
        deploy(private_key, address)

    elif args.cmd == "mint":
        metadata = {
            "standard": "arc69",
            "description": f"{args.name} — minted on Muse v2",
            "mime_type": "image/png",
            "properties": {"name": args.name},
        }
        mint_nft(
            private_key, address,
            name=args.name, unit_name=args.unit,
            metadata_url=args.url, metadata_json=metadata,
            price_algo=args.price, royalty_pct=args.royalty,
            floor_pct=args.floor, decay_months=args.decay_months,
            buyout_algo=args.buyout,
        )

    elif args.cmd == "mint-rwa":
        metadata = {
            "standard": "arc69",
            "description": f"{args.name} — RWA on Muse v2",
            "mime_type": "image/jpeg",
            "properties": {"name": args.name, "type": "physical_collectible"},
        }
        mint_nft(
            private_key, address,
            name=args.name, unit_name=args.unit,
            metadata_url=args.url, metadata_json=metadata,
            price_algo=args.price, royalty_pct=args.royalty,
            floor_pct=args.floor, decay_months=args.decay_months,
            buyout_algo=args.buyout, is_rwa=True,
            physical_hash=args.phash,
            custodian=args.custodian,
            authenticator=args.authenticator,
        )

    elif args.cmd == "co-creators":
        splits = json.loads(args.splits)
        register_co_creators(private_key, address, args.asset, splits)

    elif args.cmd == "accept":
        accept_collaboration(private_key, address, args.asset)

    elif args.cmd == "buy":
        buy_nft(private_key, address, args.asset, args.price)

    elif args.cmd == "buyout":
        buy_out_royalty(private_key, address, args.asset, args.amount)

    elif args.cmd == "auction":
        start_auction(private_key, address, args.asset, args.duration, args.reserve)

    elif args.cmd == "bid":
        place_bid(private_key, address, args.asset, args.amount)

    elif args.cmd == "settle":
        settle_auction(private_key, address, args.asset)

    elif args.cmd == "auth-rwa":
        validate_rwa(private_key, address, args.asset)

    elif args.cmd == "redeem-rwa":
        redeem_rwa(private_key, address, args.asset, args.tracking)

    elif args.cmd == "info":
        query_nft(args.asset)

    elif args.cmd == "stats":
        query_stats()


if __name__ == "__main__":
    main()
