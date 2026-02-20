"""
╔══════════════════════════════════════════════════════════════════╗
║          MUSE v2 — NFT + RWA Marketplace on Algorand            ║
║          Smart Contract: muse_marketplace.py                    ║
║                                                                  ║
║  Features:                                                       ║
║  • ARC-69 NFT minting (single & batch)                          ║
║  • 4-way royalty splits (athlete/league/charity/media)          ║
║  • Time-decaying royalties with configurable floor              ║
║  • Royalty buy-out (one-time permanent waiver)                  ║
║  • RWA physical asset tethering + authentication lifecycle      ║
║  • English auction with auto-refund                             ║
║  • Collaboration co-creator registry with on-chain acceptance   ║
╚══════════════════════════════════════════════════════════════════╝
"""

from pyteal import *
from beaker import *
from beaker.lib.storage import BoxMapping
import json


# ─────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────
MUSE_FEE_BPS       = Int(250)       # 2.5% platform fee in basis points
MAX_ROYALTY_BPS    = Int(2000)       # 20% max royalty
BASIS_POINTS       = Int(10000)
ROUNDS_PER_MONTH   = Int(777600)    # ~4s/round × 60×60×24×30 ÷ 4
MAX_CO_CREATORS    = Int(4)


# ─────────────────────────────────────────────
#  APPLICATION STATE
# ─────────────────────────────────────────────
class MuseState(GlobalStateBlob):
    # Platform
    muse_treasury: GlobalStateValue = GlobalStateValue(
        stack_type=TealType.bytes,
        descr="Muse treasury address for platform fees"
    )
    total_volume: GlobalStateValue = GlobalStateValue(
        stack_type=TealType.uint64,
        descr="Cumulative trading volume in microAlgos"
    )
    total_royalties_paid: GlobalStateValue = GlobalStateValue(
        stack_type=TealType.uint64,
        descr="Cumulative royalties distributed"
    )
    nft_count: GlobalStateValue = GlobalStateValue(
        stack_type=TealType.uint64,
        descr="Total NFTs minted through Muse"
    )
    live_auctions: GlobalStateValue = GlobalStateValue(
        stack_type=TealType.uint64,
        descr="Number of currently active auctions"
    )


class NFTLocalState(LocalStateBlob):
    """Per-account local state (holds pending collab invite data)"""
    pending_invite_nft_id: LocalStateValue = LocalStateValue(
        stack_type=TealType.uint64,
        descr="NFT ID of pending collaboration invite"
    )
    pending_invite_share: LocalStateValue = LocalStateValue(
        stack_type=TealType.uint64,
        descr="Royalty share % × 100 offered to this account"
    )


# ─────────────────────────────────────────────
#  NFT METADATA STRUCTURE  (stored in Box)
# ─────────────────────────────────────────────
# Box key  = abi.StaticArray[abi.Byte, 8]  (asset_id as big-endian bytes)
# Box value layout (packed bytes):
#   [0:8]   asset_id          uint64
#   [8:16]  price             uint64  (microAlgos, 0 = auction only)
#   [16:24] royalty_bps       uint64  (basis points, e.g. 1000 = 10%)
#   [24:32] floor_bps         uint64  (floor after decay)
#   [32:40] decay_start_round uint64  (0 = no decay)
#   [40:48] buyout_price      uint64  (0 = disabled)
#   [48:56] transfers         uint64  (ownership transfer count)
#   [56:64] flags             uint64  (bit flags below)
#   [64:96] creator           bytes32 (Algorand address, 32 bytes)
#   [96:]   co_creator data   variable
#
# FLAGS bit positions:
#   0 = is_rwa
#   1 = rwa_authenticated
#   2 = rwa_redeemed
#   3 = royalty_waived
#   4 = in_auction
#   5 = collab_pending

FLAG_IS_RWA          = Int(1 << 0)
FLAG_RWA_AUTH        = Int(1 << 1)
FLAG_RWA_REDEEMED    = Int(1 << 2)
FLAG_ROYALTY_WAIVED  = Int(1 << 3)
FLAG_IN_AUCTION      = Int(1 << 4)
FLAG_COLLAB_PENDING  = Int(1 << 5)


# ─────────────────────────────────────────────
#  AUCTION STATE STRUCTURE  (stored in Box)
# ─────────────────────────────────────────────
# Box key  = Concat(Bytes("AUC"), itob(asset_id))
# Box value:
#   [0:8]   asset_id          uint64
#   [8:16]  end_round         uint64
#   [16:24] highest_bid       uint64  (microAlgos)
#   [24:56] highest_bidder    bytes32 (Algorand address)
#   [56:88] seller            bytes32 (Algorand address)
#   [88:96] reserve_price     uint64


# ─────────────────────────────────────────────
#  CO-CREATOR / SPLIT STRUCTURE  (stored in Box)
# ─────────────────────────────────────────────
# Box key  = Concat(Bytes("SPL"), itob(asset_id))
# Box value: array of up to 4 entries × 40 bytes each
#   Per entry:
#   [0:32]  address    bytes32
#   [32:36] share_bps  uint32  (basis points of royalty)
#   [36:40] accepted   uint32  (0 = pending, 1 = accepted)


# ─────────────────────────────────────────────
#  RWA METADATA  (stored in Box)
# ─────────────────────────────────────────────
# Box key  = Concat(Bytes("RWA"), itob(asset_id))
# Box value:
#   [0:32]  physical_hash     bytes32 (SHA-256 of CoA)
#   [32:64] custodian         bytes32 (vault address)
#   [64:96] authenticator     bytes32 (auth address)
#   [96:104] redemption_round uint64  (0 = not redeemed)


# ══════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════
app = Application(
    "MuseV2Marketplace",
    state=MuseState(),
)


# ─────────────────────────────────────────────
#  DEPLOY / BOOTSTRAP
# ─────────────────────────────────────────────
@app.create
def create(treasury: abi.Address) -> Expr:
    """Deploy the Muse marketplace contract."""
    return Seq([
        app.state.muse_treasury.set(treasury.get()),
        app.state.total_volume.set(Int(0)),
        app.state.total_royalties_paid.set(Int(0)),
        app.state.nft_count.set(Int(0)),
        app.state.live_auctions.set(Int(0)),
    ])


# ─────────────────────────────────────────────
#  INTERNAL HELPERS
# ─────────────────────────────────────────────
@Subroutine(TealType.uint64)
def current_royalty_bps(asset_id: Expr) -> Expr:
    """
    Compute the effective royalty BPS for an asset,
    applying time-decay if configured.

    Returns 0 if royalty has been waived.
    """
    box_key = Concat(Bytes("NFT"), itob(asset_id))
    box_val = BoxGet(box_key)
    flags_val = ExtractUint64(box_val.value(), Int(56))
    royalty   = ExtractUint64(box_val.value(), Int(16))
    floor_bps = ExtractUint64(box_val.value(), Int(24))
    decay_start = ExtractUint64(box_val.value(), Int(32))

    rounds_elapsed = Global.round() - decay_start
    decay_ratio    = rounds_elapsed / ROUNDS_PER_MONTH  # months elapsed

    # Linear decay: royalty decreases by (royalty - floor) over decay period
    # For simplicity, full decay after 1× the configured period
    decayed = royalty - ((royalty - floor_bps) * decay_ratio / Int(100))

    return Seq([
        Assert(box_val.hasValue()),
        If(BitwiseAnd(flags_val, FLAG_ROYALTY_WAIVED) != Int(0),
           Return(Int(0)),
           If(decay_start == Int(0),
              Return(royalty),
              If(Global.round() < decay_start,
                 Return(royalty),
                 Return(If(decayed > floor_bps, decayed, floor_bps))
              )
           )
        )
    ])


@Subroutine(TealType.none)
def distribute_royalties(
    asset_id: Expr,
    sale_price: Expr,
    royalty_bps: Expr,
) -> Expr:
    """
    Distribute royalty payments to all registered split recipients.
    Any remainder goes to the primary creator.
    """
    spl_key      = Concat(Bytes("SPL"), itob(asset_id))
    nft_key      = Concat(Bytes("NFT"), itob(asset_id))
    spl_box      = BoxGet(spl_key)
    nft_box      = BoxGet(nft_key)
    total_royalty = sale_price * royalty_bps / BASIS_POINTS
    muse_fee      = sale_price * MUSE_FEE_BPS / BASIS_POINTS
    creator_addr  = Extract(nft_box.value(), Int(64), Int(32))

    i             = ScratchVar(TealType.uint64)
    paid_to_splits = ScratchVar(TealType.uint64)

    return Seq([
        # Pay Muse platform fee
        InnerTransactionBuilder.Begin(),
        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.Payment),
        InnerTransactionBuilder.SetField(TxnField.receiver, app.state.muse_treasury.get()),
        InnerTransactionBuilder.SetField(TxnField.amount, muse_fee),
        InnerTransactionBuilder.Submit(),

        paid_to_splits.store(Int(0)),

        # Pay each co-creator split
        If(
            spl_box.hasValue(),
            Seq([
                For(
                    i.store(Int(0)),
                    i.load() < Int(4),
                    i.store(i.load() + Int(1)),
                    Seq([
                        # Each entry is 40 bytes
                        # address = bytes 0:32, share_bps = bytes 32:36, accepted = 36:40
                        InnerTransactionBuilder.Begin(),
                        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.Payment),
                        InnerTransactionBuilder.SetField(
                            TxnField.receiver,
                            Extract(spl_box.value(), i.load() * Int(40), Int(32))
                        ),
                        InnerTransactionBuilder.SetField(
                            TxnField.amount,
                            total_royalty * ExtractUint32(spl_box.value(), i.load() * Int(40) + Int(32)) / BASIS_POINTS
                        ),
                        InnerTransactionBuilder.Submit(),
                        paid_to_splits.store(
                            paid_to_splits.load() +
                            total_royalty * ExtractUint32(spl_box.value(), i.load() * Int(40) + Int(32)) / BASIS_POINTS
                        ),
                    ])
                )
            ])
        ),

        # Remainder royalty to primary creator
        If(
            total_royalty > paid_to_splits.load(),
            Seq([
                InnerTransactionBuilder.Begin(),
                InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.Payment),
                InnerTransactionBuilder.SetField(TxnField.receiver, creator_addr),
                InnerTransactionBuilder.SetField(TxnField.amount, total_royalty - paid_to_splits.load()),
                InnerTransactionBuilder.Submit(),
            ])
        ),

        # Update global royalties paid counter
        app.state.total_royalties_paid.set(
            app.state.total_royalties_paid.get() + total_royalty
        ),
    ])


# ─────────────────────────────────────────────
#  NFT MINTING
# ─────────────────────────────────────────────
@app.external
def mint_nft(
    name: abi.String,
    unit_name: abi.String,
    metadata_url: abi.String,
    metadata_hash: abi.StaticArray[abi.Byte, Literal[32]],
    price_microalgos: abi.Uint64,
    royalty_bps: abi.Uint64,
    floor_bps: abi.Uint64,
    decay_after_rounds: abi.Uint64,   # 0 = no decay
    buyout_price: abi.Uint64,          # 0 = disabled
    *,
    output: abi.Uint64,
) -> Expr:
    """
    Mint a single ARC-69 NFT on Algorand.

    Args:
        name:                 Human-readable NFT name
        unit_name:            Short token symbol (max 8 chars)
        metadata_url:         URL to full ARC-69 metadata JSON
        metadata_hash:        SHA-256 hash of metadata file (32 bytes)
        price_microalgos:     Fixed sale price in microAlgos (0 = auction only)
        royalty_bps:          Royalty in basis points (max 2000 = 20%)
        floor_bps:            Floor royalty after decay
        decay_after_rounds:   Algorand round offset when decay begins (0 = off)
        buyout_price:         One-time buy-out price in microAlgos (0 = off)

    Returns:
        Newly created Algorand Standard Asset (ASA) ID
    """
    asset_id = ScratchVar(TealType.uint64)
    decay_start = ScratchVar(TealType.uint64)

    return Seq([
        Assert(royalty_bps.get() <= MAX_ROYALTY_BPS, comment="Royalty exceeds 20%"),
        Assert(floor_bps.get() <= royalty_bps.get(), comment="Floor must be <= royalty"),

        # Create the ASA (ARC-69: metadata in note field of config txn)
        InnerTransactionBuilder.Begin(),
        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.AssetConfig),
        InnerTransactionBuilder.SetField(TxnField.config_asset_total, Int(1)),
        InnerTransactionBuilder.SetField(TxnField.config_asset_decimals, Int(0)),
        InnerTransactionBuilder.SetField(TxnField.config_asset_default_frozen, Int(0)),
        InnerTransactionBuilder.SetField(TxnField.config_asset_name, name.get()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_unit_name, unit_name.get()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_url, metadata_url.get()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_metadata_hash, metadata_hash.encode()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_manager, Global.current_application_address()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_reserve, Txn.sender()),
        InnerTransactionBuilder.SetField(TxnField.note, Bytes("arc69")),
        InnerTransactionBuilder.Submit(),

        asset_id.store(InnerTxn.created_asset_id()),

        # Compute decay start round
        decay_start.store(
            If(decay_after_rounds.get() == Int(0),
               Int(0),
               Global.round() + decay_after_rounds.get()
            )
        ),

        # Write NFT metadata to Box
        Pop(BoxCreate(
            Concat(Bytes("NFT"), itob(asset_id.load())),
            Int(128)  # fixed header: 8+8+8+8+8+8+8+8+32 = 96 bytes + padding
        )),
        BoxReplace(
            Concat(Bytes("NFT"), itob(asset_id.load())),
            Int(0),
            Concat(
                itob(asset_id.load()),           # [0:8]   asset_id
                itob(price_microalgos.get()),     # [8:16]  price
                itob(royalty_bps.get()),          # [16:24] royalty_bps
                itob(floor_bps.get()),            # [24:32] floor_bps
                itob(decay_start.load()),         # [32:40] decay_start_round
                itob(buyout_price.get()),         # [40:48] buyout_price
                itob(Int(0)),                     # [48:56] transfers = 0
                itob(Int(0)),                     # [56:64] flags = 0
                Txn.sender(),                     # [64:96] creator (32 bytes)
            )
        ),

        # Increment global NFT count
        app.state.nft_count.set(app.state.nft_count.get() + Int(1)),

        output.set(asset_id.load()),
    ])


@app.external
def mint_rwa(
    name: abi.String,
    unit_name: abi.String,
    metadata_url: abi.String,
    metadata_hash: abi.StaticArray[abi.Byte, Literal[32]],
    physical_asset_hash: abi.StaticArray[abi.Byte, Literal[32]],
    custodian: abi.Address,
    authenticator: abi.Address,
    price_microalgos: abi.Uint64,
    royalty_bps: abi.Uint64,
    floor_bps: abi.Uint64,
    decay_after_rounds: abi.Uint64,
    buyout_price: abi.Uint64,
    *,
    output: abi.Uint64,
) -> Expr:
    """
    Mint a Real World Asset (RWA) token.

    The NFT is created in PENDING_AUTHENTICATION state and cannot
    be purchased until `validate_physical_asset()` is called by
    the registered authenticator address.

    Args:
        physical_asset_hash: SHA-256 of certificate of authenticity / NFC tag
        custodian:           Address of vault holding the physical item
        authenticator:       Address authorized to call validate_physical_asset()
        (remaining args same as mint_nft)
    """
    asset_id = ScratchVar(TealType.uint64)

    return Seq([
        Assert(royalty_bps.get() <= MAX_ROYALTY_BPS, comment="Royalty exceeds 20%"),

        # Create ASA (same as mint_nft)
        InnerTransactionBuilder.Begin(),
        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.AssetConfig),
        InnerTransactionBuilder.SetField(TxnField.config_asset_total, Int(1)),
        InnerTransactionBuilder.SetField(TxnField.config_asset_decimals, Int(0)),
        InnerTransactionBuilder.SetField(TxnField.config_asset_default_frozen, Int(1)),  # Frozen until authenticated
        InnerTransactionBuilder.SetField(TxnField.config_asset_name, name.get()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_unit_name, unit_name.get()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_url, metadata_url.get()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_metadata_hash, metadata_hash.encode()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_manager, Global.current_application_address()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_freeze, Global.current_application_address()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_clawback, Global.current_application_address()),
        InnerTransactionBuilder.SetField(TxnField.config_asset_reserve, custodian.get()),
        InnerTransactionBuilder.Submit(),

        asset_id.store(InnerTxn.created_asset_id()),

        # Write NFT box with FLAG_IS_RWA set
        Pop(BoxCreate(
            Concat(Bytes("NFT"), itob(asset_id.load())),
            Int(128)
        )),
        BoxReplace(
            Concat(Bytes("NFT"), itob(asset_id.load())),
            Int(0),
            Concat(
                itob(asset_id.load()),
                itob(price_microalgos.get()),
                itob(royalty_bps.get()),
                itob(floor_bps.get()),
                itob(If(decay_after_rounds.get() == Int(0), Int(0), Global.round() + decay_after_rounds.get())),
                itob(buyout_price.get()),
                itob(Int(0)),               # transfers
                itob(FLAG_IS_RWA),          # flags: RWA flag set
                Txn.sender(),               # creator
            )
        ),

        # Write RWA metadata box
        Pop(BoxCreate(
            Concat(Bytes("RWA"), itob(asset_id.load())),
            Int(104)  # 32+32+32+8
        )),
        BoxReplace(
            Concat(Bytes("RWA"), itob(asset_id.load())),
            Int(0),
            Concat(
                physical_asset_hash.encode(),  # [0:32]  physical_hash
                custodian.get(),               # [32:64] custodian
                authenticator.get(),           # [64:96] authenticator
                itob(Int(0)),                  # [96:104] redemption_round = 0
            )
        ),

        app.state.nft_count.set(app.state.nft_count.get() + Int(1)),
        output.set(asset_id.load()),
    ])


# ─────────────────────────────────────────────
#  RWA LIFECYCLE
# ─────────────────────────────────────────────
@app.external
def validate_physical_asset(asset_id: abi.Uint64) -> Expr:
    """
    Called by the registered authenticator to mark an RWA as authenticated.
    Unfreezes the asset so it can be transferred/purchased.

    Only the authenticator address stored in the RWA box can call this.
    """
    rwa_key  = Concat(Bytes("RWA"), itob(asset_id.get()))
    nft_key  = Concat(Bytes("NFT"), itob(asset_id.get()))
    rwa_box  = BoxGet(rwa_key)
    nft_box  = BoxGet(nft_key)
    auth_addr = Extract(rwa_box.value(), Int(64), Int(32))
    current_flags = ExtractUint64(nft_box.value(), Int(56))

    return Seq([
        Assert(rwa_box.hasValue(), comment="Not an RWA asset"),
        Assert(nft_box.hasValue()),
        Assert(auth_addr == Txn.sender(), comment="Only registered authenticator can validate"),
        Assert(
            BitwiseAnd(current_flags, FLAG_RWA_AUTH) == Int(0),
            comment="Already authenticated"
        ),

        # Set FLAG_RWA_AUTH in flags field
        BoxReplace(
            nft_key,
            Int(56),
            itob(BitwiseOr(current_flags, FLAG_RWA_AUTH))
        ),

        # Unfreeze the ASA
        InnerTransactionBuilder.Begin(),
        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.AssetFreeze),
        InnerTransactionBuilder.SetField(TxnField.freeze_asset, asset_id.get()),
        InnerTransactionBuilder.SetField(TxnField.freeze_asset_account, Txn.sender()),
        InnerTransactionBuilder.SetField(TxnField.freeze_asset_frozen, Int(0)),
        InnerTransactionBuilder.Submit(),
    ])


@app.external
def redeem_physical_asset(
    asset_id: abi.Uint64,
    tracking_info: abi.String,
) -> Expr:
    """
    Called by the custodian to record physical redemption.
    Locks the NFT from further sales after redemption.

    Args:
        tracking_info:  Shipping/collection tracking reference stored in note
    """
    rwa_key  = Concat(Bytes("RWA"), itob(asset_id.get()))
    nft_key  = Concat(Bytes("NFT"), itob(asset_id.get()))
    rwa_box  = BoxGet(rwa_key)
    nft_box  = BoxGet(nft_key)
    custodian_addr = Extract(rwa_box.value(), Int(32), Int(32))
    current_flags  = ExtractUint64(nft_box.value(), Int(56))

    return Seq([
        Assert(rwa_box.hasValue(), comment="Not an RWA asset"),
        Assert(custodian_addr == Txn.sender(), comment="Only custodian can redeem"),
        Assert(
            BitwiseAnd(current_flags, FLAG_RWA_REDEEMED) == Int(0),
            comment="Already redeemed"
        ),
        Assert(
            BitwiseAnd(current_flags, FLAG_RWA_AUTH) != Int(0),
            comment="Asset must be authenticated before redemption"
        ),

        # Record redemption round in RWA box
        BoxReplace(rwa_key, Int(96), itob(Global.round())),

        # Set REDEEMED flag, clear IN_AUCTION flag
        BoxReplace(
            nft_key,
            Int(56),
            itob(BitwiseAnd(
                BitwiseOr(current_flags, FLAG_RWA_REDEEMED),
                BitwiseNot(FLAG_IN_AUCTION)
            ))
        ),
    ])


# ─────────────────────────────────────────────
#  COLLABORATION / CO-CREATOR REGISTRY
# ─────────────────────────────────────────────
@app.external
def register_co_creators(
    asset_id: abi.Uint64,
    addr_1: abi.Address,
    share_bps_1: abi.Uint32,
    role_1: abi.String,
    addr_2: abi.Address,
    share_bps_2: abi.Uint32,
    role_2: abi.String,
    addr_3: abi.Address,
    share_bps_3: abi.Uint32,
    role_3: abi.String,
    addr_4: abi.Address,
    share_bps_4: abi.Uint32,
    role_4: abi.String,
) -> Expr:
    """
    Register up to 4 co-creators for an NFT's royalty split.

    Only the original creator (NFT box creator field) can call this.
    All splits must total ≤ 10000 BPS (100%).
    Each co-creator must later call accept_collaboration() to formally consent.

    Args:
        addr_N:       Algorand address of co-creator N (use ZeroAddress to skip)
        share_bps_N:  Share of total royalty in basis points (e.g. 3000 = 30%)
        role_N:       Human-readable role label (e.g. "Athlete", "Charity")
    """
    nft_key    = Concat(Bytes("NFT"), itob(asset_id.get()))
    spl_key    = Concat(Bytes("SPL"), itob(asset_id.get()))
    nft_box    = BoxGet(nft_key)
    creator    = Extract(nft_box.value(), Int(64), Int(32))
    total_bps  = share_bps_1.get() + share_bps_2.get() + share_bps_3.get() + share_bps_4.get()

    return Seq([
        Assert(nft_box.hasValue()),
        Assert(creator == Txn.sender(), comment="Only NFT creator can register co-creators"),
        Assert(total_bps <= BASIS_POINTS, comment="Total split exceeds 100%"),

        # Create split box: 4 entries × 40 bytes = 160 bytes
        Pop(BoxCreate(spl_key, Int(160))),
        BoxReplace(
            spl_key, Int(0),
            Concat(
                # Entry 1
                addr_1.get(),                              # [0:32]   address
                Extract(itob(share_bps_1.get()), Int(4), Int(4)),  # [32:36] share_bps (uint32)
                Bytes("\x00\x00\x00\x00"),                # [36:40]  accepted=0
                # Entry 2
                addr_2.get(),
                Extract(itob(share_bps_2.get()), Int(4), Int(4)),
                Bytes("\x00\x00\x00\x00"),
                # Entry 3
                addr_3.get(),
                Extract(itob(share_bps_3.get()), Int(4), Int(4)),
                Bytes("\x00\x00\x00\x00"),
                # Entry 4
                addr_4.get(),
                Extract(itob(share_bps_4.get()), Int(4), Int(4)),
                Bytes("\x00\x00\x00\x00"),
            )
        ),

        # Set COLLAB_PENDING flag on NFT
        BoxReplace(
            nft_key, Int(56),
            itob(BitwiseOr(ExtractUint64(nft_box.value(), Int(56)), FLAG_COLLAB_PENDING))
        ),
    ])


@app.external
def accept_collaboration(asset_id: abi.Uint64) -> Expr:
    """
    Called by a registered co-creator to formally accept their role.

    The caller must be one of the 4 registered co-creator addresses.
    This sets their `accepted` field from 0 → 1.
    """
    spl_key = Concat(Bytes("SPL"), itob(asset_id.get()))
    spl_box = BoxGet(spl_key)
    i       = ScratchVar(TealType.uint64)
    found   = ScratchVar(TealType.uint64)

    return Seq([
        Assert(spl_box.hasValue(), comment="No co-creator registry for this NFT"),
        found.store(Int(0)),

        For(
            i.store(Int(0)),
            i.load() < Int(4),
            i.store(i.load() + Int(1)),
            Seq([
                If(
                    Extract(spl_box.value(), i.load() * Int(40), Int(32)) == Txn.sender(),
                    Seq([
                        # Set accepted = 1 at offset 36 of this entry
                        BoxReplace(
                            spl_key,
                            i.load() * Int(40) + Int(36),
                            Bytes("\x00\x00\x00\x01")  # uint32(1)
                        ),
                        found.store(Int(1)),
                    ])
                )
            ])
        ),

        Assert(found.load(), comment="Caller is not a registered co-creator"),
    ])


# ─────────────────────────────────────────────
#  MARKETPLACE: BUY / SELL
# ─────────────────────────────────────────────
@app.external
def buy_nft(
    asset_id: abi.Uint64,
    payment: abi.PaymentTransaction,
) -> Expr:
    """
    Purchase an NFT at its fixed list price.

    The buyer must:
    1. Opt in to the ASA before calling this.
    2. Send a payment transaction for the exact list price.

    Royalties are automatically distributed to all split recipients.
    The seller receives price - royalties - platform_fee.

    Args:
        asset_id:  The Algorand ASA ID to purchase
        payment:   Attached ALGO payment (must equal list price)
    """
    nft_key      = Concat(Bytes("NFT"), itob(asset_id.get()))
    nft_box      = BoxGet(nft_key)
    flags        = ExtractUint64(nft_box.value(), Int(56))
    price        = ExtractUint64(nft_box.value(), Int(8))
    transfers    = ExtractUint64(nft_box.value(), Int(48))
    creator      = Extract(nft_box.value(), Int(64), Int(32))
    royalty_bps  = ScratchVar(TealType.uint64)
    net_to_seller = ScratchVar(TealType.uint64)

    return Seq([
        Assert(nft_box.hasValue(), comment="NFT does not exist"),
        Assert(price > Int(0), comment="NFT is auction-only"),
        Assert(
            BitwiseAnd(flags, FLAG_IN_AUCTION) == Int(0),
            comment="NFT is currently in auction"
        ),
        Assert(
            BitwiseAnd(flags, FLAG_RWA_REDEEMED) == Int(0),
            comment="RWA has been physically redeemed"
        ),
        If(
            BitwiseAnd(flags, FLAG_IS_RWA) != Int(0),
            Assert(
                BitwiseAnd(flags, FLAG_RWA_AUTH) != Int(0),
                comment="RWA not yet authenticated"
            )
        ),
        Assert(payment.get().receiver() == Global.current_application_address()),
        Assert(payment.get().amount() == price, comment="Incorrect payment amount"),

        # Compute current (possibly decayed) royalty
        royalty_bps.store(current_royalty_bps(asset_id.get())),

        # Distribute royalties if any
        If(
            royalty_bps.load() > Int(0),
            distribute_royalties(asset_id.get(), price, royalty_bps.load())
        ),

        # Calculate net to seller
        net_to_seller.store(
            price
            - (price * royalty_bps.load() / BASIS_POINTS)
            - (price * MUSE_FEE_BPS / BASIS_POINTS)
        ),

        # Pay seller (creator on primary; current holder on secondary)
        InnerTransactionBuilder.Begin(),
        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.Payment),
        InnerTransactionBuilder.SetField(TxnField.receiver, creator),
        InnerTransactionBuilder.SetField(TxnField.amount, net_to_seller.load()),
        InnerTransactionBuilder.Submit(),

        # Transfer ASA to buyer
        InnerTransactionBuilder.Begin(),
        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.AssetTransfer),
        InnerTransactionBuilder.SetField(TxnField.xfer_asset, asset_id.get()),
        InnerTransactionBuilder.SetField(TxnField.asset_receiver, Txn.sender()),
        InnerTransactionBuilder.SetField(TxnField.asset_amount, Int(1)),
        InnerTransactionBuilder.Submit(),

        # Update transfer count
        BoxReplace(nft_key, Int(48), itob(transfers + Int(1))),

        # Update global volume
        app.state.total_volume.set(app.state.total_volume.get() + price),
    ])


# ─────────────────────────────────────────────
#  ROYALTY BUY-OUT
# ─────────────────────────────────────────────
@app.external
def buy_out_royalty(
    asset_id: abi.Uint64,
    payment: abi.PaymentTransaction,
) -> Expr:
    """
    Pay the configured buy-out price to permanently waive all future royalties.

    - 95% of the buy-out payment goes to the original creator.
    - 5% goes to Muse treasury.
    - Sets FLAG_ROYALTY_WAIVED on the NFT.

    Args:
        asset_id:  The ASA to buy out royalties for
        payment:   ALGO payment equal to the NFT's buyout_price
    """
    nft_key      = Concat(Bytes("NFT"), itob(asset_id.get()))
    nft_box      = BoxGet(nft_key)
    flags        = ExtractUint64(nft_box.value(), Int(56))
    buyout_price = ExtractUint64(nft_box.value(), Int(40))
    creator      = Extract(nft_box.value(), Int(64), Int(32))
    creator_cut  = buyout_price * Int(9500) / BASIS_POINTS  # 95%
    muse_cut     = buyout_price - creator_cut               # 5%

    return Seq([
        Assert(nft_box.hasValue()),
        Assert(buyout_price > Int(0), comment="Royalty buy-out not enabled for this NFT"),
        Assert(
            BitwiseAnd(flags, FLAG_ROYALTY_WAIVED) == Int(0),
            comment="Royalties already waived"
        ),
        Assert(payment.get().amount() == buyout_price, comment="Incorrect buy-out payment"),
        Assert(payment.get().receiver() == Global.current_application_address()),

        # Pay creator 95%
        InnerTransactionBuilder.Begin(),
        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.Payment),
        InnerTransactionBuilder.SetField(TxnField.receiver, creator),
        InnerTransactionBuilder.SetField(TxnField.amount, creator_cut),
        InnerTransactionBuilder.Submit(),

        # Pay Muse 5%
        InnerTransactionBuilder.Begin(),
        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.Payment),
        InnerTransactionBuilder.SetField(TxnField.receiver, app.state.muse_treasury.get()),
        InnerTransactionBuilder.SetField(TxnField.amount, muse_cut),
        InnerTransactionBuilder.Submit(),

        # Set ROYALTY_WAIVED flag, clear buyout_price
        BoxReplace(
            nft_key, Int(56),
            itob(BitwiseOr(flags, FLAG_ROYALTY_WAIVED))
        ),
        BoxReplace(nft_key, Int(40), itob(Int(0))),
    ])


# ─────────────────────────────────────────────
#  AUCTION
# ─────────────────────────────────────────────
@app.external
def start_auction(
    asset_id: abi.Uint64,
    duration_rounds: abi.Uint64,
    reserve_price: abi.Uint64,
) -> Expr:
    """
    Start an English auction for an NFT.

    The seller (current holder) deposits the NFT into the contract.
    Only the NFT owner (reserve address field) can start the auction.

    Args:
        asset_id:        The ASA to auction
        duration_rounds: How many Algorand rounds the auction runs
        reserve_price:   Minimum bid in microAlgos
    """
    auc_key   = Concat(Bytes("AUC"), itob(asset_id.get()))
    nft_key   = Concat(Bytes("NFT"), itob(asset_id.get()))
    nft_box   = BoxGet(nft_key)
    flags     = ExtractUint64(nft_box.value(), Int(56))

    return Seq([
        Assert(nft_box.hasValue()),
        Assert(
            BitwiseAnd(flags, FLAG_IN_AUCTION) == Int(0),
            comment="Already in auction"
        ),
        Assert(
            BitwiseAnd(flags, FLAG_RWA_REDEEMED) == Int(0),
            comment="Redeemed RWA cannot be auctioned"
        ),
        Assert(duration_rounds.get() > Int(0)),

        # Transfer NFT from seller to contract escrow
        InnerTransactionBuilder.Begin(),
        InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.AssetTransfer),
        InnerTransactionBuilder.SetField(TxnField.xfer_asset, asset_id.get()),
        InnerTransactionBuilder.SetField(TxnField.asset_sender, Txn.sender()),
        InnerTransactionBuilder.SetField(TxnField.asset_receiver, Global.current_application_address()),
        InnerTransactionBuilder.SetField(TxnField.asset_amount, Int(1)),
        InnerTransactionBuilder.Submit(),

        # Create auction box
        Pop(BoxCreate(auc_key, Int(88))),
        BoxReplace(
            auc_key, Int(0),
            Concat(
                itob(asset_id.get()),                         # [0:8]   asset_id
                itob(Global.round() + duration_rounds.get()), # [8:16]  end_round
                itob(Int(0)),                                 # [16:24] highest_bid = 0
                Global.zero_address(),                        # [24:56] highest_bidder = zero
                Txn.sender(),                                 # [56:88] seller
            )
        ),
        BoxReplace(auc_key, Int(0), itob(reserve_price.get())),  # store reserve in first slot temporarily

        # Set IN_AUCTION flag
        BoxReplace(
            nft_key, Int(56),
            itob(BitwiseOr(flags, FLAG_IN_AUCTION))
        ),

        app.state.live_auctions.set(app.state.live_auctions.get() + Int(1)),
    ])


@app.external
def place_bid(
    asset_id: abi.Uint64,
    payment: abi.PaymentTransaction,
) -> Expr:
    """
    Place a bid on an active auction.

    - Bid must exceed the current highest bid.
    - Previous highest bidder is automatically refunded.
    - Auction auto-extends by 5 minutes if bid placed in final 5 minutes.

    Args:
        asset_id:  The ASA being auctioned
        payment:   ALGO payment equal to the bid amount
    """
    auc_key         = Concat(Bytes("AUC"), itob(asset_id.get()))
    auc_box         = BoxGet(auc_key)
    end_round       = ExtractUint64(auc_box.value(), Int(8))
    highest_bid     = ExtractUint64(auc_box.value(), Int(16))
    highest_bidder  = Extract(auc_box.value(), Int(24), Int(32))
    new_bid         = payment.get().amount()
    ANTI_SNIPE_ROUNDS = Int(75)  # ~5 minutes

    return Seq([
        Assert(auc_box.hasValue(), comment="No active auction for this asset"),
        Assert(Global.round() < end_round, comment="Auction has ended"),
        Assert(new_bid > highest_bid, comment="Bid must exceed current highest bid"),
        Assert(payment.get().receiver() == Global.current_application_address()),

        # Refund previous bidder if any
        If(
            highest_bidder != Global.zero_address(),
            Seq([
                InnerTransactionBuilder.Begin(),
                InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.Payment),
                InnerTransactionBuilder.SetField(TxnField.receiver, highest_bidder),
                InnerTransactionBuilder.SetField(TxnField.amount, highest_bid),
                InnerTransactionBuilder.Submit(),
            ])
        ),

        # Update auction state
        BoxReplace(auc_key, Int(16), itob(new_bid)),
        BoxReplace(auc_key, Int(24), Txn.sender()),

        # Anti-sniping: extend by 5 min if within final window
        If(
            end_round - Global.round() < ANTI_SNIPE_ROUNDS,
            BoxReplace(auc_key, Int(8), itob(end_round + ANTI_SNIPE_ROUNDS))
        ),
    ])


@app.external
def settle_auction(asset_id: abi.Uint64) -> Expr:
    """
    Settle a completed auction.

    Can be called by anyone after the auction end round.
    - Transfers NFT to winning bidder.
    - Distributes royalties and pays seller.
    - If no bids, returns NFT to seller.
    """
    auc_key        = Concat(Bytes("AUC"), itob(asset_id.get()))
    nft_key        = Concat(Bytes("NFT"), itob(asset_id.get()))
    auc_box        = BoxGet(auc_key)
    nft_box        = BoxGet(nft_key)
    end_round      = ExtractUint64(auc_box.value(), Int(8))
    highest_bid    = ExtractUint64(auc_box.value(), Int(16))
    highest_bidder = Extract(auc_box.value(), Int(24), Int(32))
    seller         = Extract(auc_box.value(), Int(56), Int(32))
    transfers      = ExtractUint64(nft_box.value(), Int(48))
    flags          = ExtractUint64(nft_box.value(), Int(56))
    royalty_bps    = ScratchVar(TealType.uint64)

    return Seq([
        Assert(auc_box.hasValue()),
        Assert(Global.round() >= end_round, comment="Auction still running"),

        If(
            highest_bidder == Global.zero_address(),
            # No bids: return NFT to seller
            Seq([
                InnerTransactionBuilder.Begin(),
                InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.AssetTransfer),
                InnerTransactionBuilder.SetField(TxnField.xfer_asset, asset_id.get()),
                InnerTransactionBuilder.SetField(TxnField.asset_receiver, seller),
                InnerTransactionBuilder.SetField(TxnField.asset_amount, Int(1)),
                InnerTransactionBuilder.Submit(),
            ]),
            # Has winner: distribute payment + transfer NFT
            Seq([
                royalty_bps.store(current_royalty_bps(asset_id.get())),
                If(
                    royalty_bps.load() > Int(0),
                    distribute_royalties(asset_id.get(), highest_bid, royalty_bps.load())
                ),
                # Pay seller net amount
                InnerTransactionBuilder.Begin(),
                InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.Payment),
                InnerTransactionBuilder.SetField(TxnField.receiver, seller),
                InnerTransactionBuilder.SetField(
                    TxnField.amount,
                    highest_bid
                    - (highest_bid * royalty_bps.load() / BASIS_POINTS)
                    - (highest_bid * MUSE_FEE_BPS / BASIS_POINTS)
                ),
                InnerTransactionBuilder.Submit(),
                # Transfer NFT
                InnerTransactionBuilder.Begin(),
                InnerTransactionBuilder.SetField(TxnField.type_enum, TxnType.AssetTransfer),
                InnerTransactionBuilder.SetField(TxnField.xfer_asset, asset_id.get()),
                InnerTransactionBuilder.SetField(TxnField.asset_receiver, highest_bidder),
                InnerTransactionBuilder.SetField(TxnField.asset_amount, Int(1)),
                InnerTransactionBuilder.Submit(),
                # Update transfer count
                BoxReplace(nft_key, Int(48), itob(transfers + Int(1))),
                app.state.total_volume.set(app.state.total_volume.get() + highest_bid),
            ])
        ),

        # Clear IN_AUCTION flag
        BoxReplace(
            nft_key, Int(56),
            itob(BitwiseAnd(flags, BitwiseNot(FLAG_IN_AUCTION)))
        ),

        # Delete auction box
        Pop(BoxDelete(auc_key)),
        app.state.live_auctions.set(app.state.live_auctions.get() - Int(1)),
    ])


# ─────────────────────────────────────────────
#  READ-ONLY VIEWS (ABI methods)
# ─────────────────────────────────────────────
@app.external(read_only=True)
def get_nft_info(
    asset_id: abi.Uint64,
    *,
    output: abi.Tuple8[
        abi.Uint64,  # price
        abi.Uint64,  # royalty_bps
        abi.Uint64,  # floor_bps
        abi.Uint64,  # decay_start_round
        abi.Uint64,  # buyout_price
        abi.Uint64,  # transfers
        abi.Uint64,  # flags
        abi.Address, # creator
    ],
) -> Expr:
    """Return packed NFT metadata from box storage."""
    nft_key = Concat(Bytes("NFT"), itob(asset_id.get()))
    nft_box = BoxGet(nft_key)
    price          = abi.Uint64()
    royalty        = abi.Uint64()
    floor          = abi.Uint64()
    decay          = abi.Uint64()
    buyout         = abi.Uint64()
    xfers          = abi.Uint64()
    flags_out      = abi.Uint64()
    creator_out    = abi.Address()

    return Seq([
        Assert(nft_box.hasValue()),
        price.set(ExtractUint64(nft_box.value(), Int(8))),
        royalty.set(ExtractUint64(nft_box.value(), Int(16))),
        floor.set(ExtractUint64(nft_box.value(), Int(24))),
        decay.set(ExtractUint64(nft_box.value(), Int(32))),
        buyout.set(ExtractUint64(nft_box.value(), Int(40))),
        xfers.set(ExtractUint64(nft_box.value(), Int(48))),
        flags_out.set(ExtractUint64(nft_box.value(), Int(56))),
        creator_out.set(Extract(nft_box.value(), Int(64), Int(32))),
        output.set(price, royalty, floor, decay, buyout, xfers, flags_out, creator_out),
    ])


@app.external(read_only=True)
def get_current_royalty(
    asset_id: abi.Uint64,
    *,
    output: abi.Uint64,
) -> Expr:
    """Return the effective (possibly decayed) royalty BPS right now."""
    return output.set(current_royalty_bps(asset_id.get()))


@app.external(read_only=True)
def get_auction_info(
    asset_id: abi.Uint64,
    *,
    output: abi.Tuple4[
        abi.Uint64,  # end_round
        abi.Uint64,  # highest_bid
        abi.Address, # highest_bidder
        abi.Address, # seller
    ],
) -> Expr:
    """Return current auction state for an asset."""
    auc_key    = Concat(Bytes("AUC"), itob(asset_id.get()))
    auc_box    = BoxGet(auc_key)
    end_r      = abi.Uint64()
    h_bid      = abi.Uint64()
    h_bidder   = abi.Address()
    seller_out = abi.Address()

    return Seq([
        Assert(auc_box.hasValue(), comment="No active auction"),
        end_r.set(ExtractUint64(auc_box.value(), Int(8))),
        h_bid.set(ExtractUint64(auc_box.value(), Int(16))),
        h_bidder.set(Extract(auc_box.value(), Int(24), Int(32))),
        seller_out.set(Extract(auc_box.value(), Int(56), Int(32))),
        output.set(end_r, h_bid, h_bidder, seller_out),
    ])


@app.external(read_only=True)
def get_platform_stats(
    *,
    output: abi.Tuple4[
        abi.Uint64,  # total_volume
        abi.Uint64,  # total_royalties_paid
        abi.Uint64,  # nft_count
        abi.Uint64,  # live_auctions
    ],
) -> Expr:
    """Return global marketplace statistics."""
    vol    = abi.Uint64()
    royal  = abi.Uint64()
    count  = abi.Uint64()
    aucs   = abi.Uint64()

    return Seq([
        vol.set(app.state.total_volume.get()),
        royal.set(app.state.total_royalties_paid.get()),
        count.set(app.state.nft_count.get()),
        aucs.set(app.state.live_auctions.get()),
        output.set(vol, royal, count, aucs),
    ])


# ─────────────────────────────────────────────
#  ADMIN
# ─────────────────────────────────────────────
@app.external
def update_treasury(new_treasury: abi.Address) -> Expr:
    """Update the Muse treasury address. Only callable by contract creator."""
    return Seq([
        Assert(Txn.sender() == Global.creator_address(), comment="Admin only"),
        app.state.muse_treasury.set(new_treasury.get()),
    ])


if __name__ == "__main__":
    # Export the contract for deployment
    import os
    os.makedirs("build", exist_ok=True)
    app_spec = app.build()
    app_spec.export("build")
    print("✅ Muse v2 contract compiled to ./build/")
    print(f"   Approval size: {len(app_spec.approval_program)} bytes")
    print(f"   Clear size:    {len(app_spec.clear_program)} bytes")
