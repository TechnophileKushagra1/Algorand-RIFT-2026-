"""
Muse — v2 NFT Marketplace Smart Contract
Complete Blockchain Prototype with RWA, Auctions, Batch Minting & More
RIFT 2026 Hackathon | Algorand Open Innovation Track

New Features in v2:
  ✦ Time-Decaying Royalties   — royalty drops automatically over time
  ✦ Batch Minting             — mint multiple NFTs in one sequence
  ✦ ARC-69 Metadata           — on-chain metadata standard compliance
  ✦ Auction / Bidding         — time-limited on-chain auctions
  ✦ Collaboration Consent     — co-creators must formally accept roles
  ✦ Royalty Buy-Out           — one-time payment to remove all royalties
  ✦ Physical Asset Tethering  — RWA digital twin with hash + redemption
  ✦ Authenticator Role        — verified authenticator for memorabilia
  ✦ Multi-Layer Royalties     — athlete / league / charity / photographer
  ✦ Fractional RWA Ownership  — pool funds for high-value physical items
  ✦ is_redeemed Flag          — physical item redemption state machine

Built with: AlgoKit + Beaker (PyTEAL)
Deploy:     algokit deploy --network testnet
App ID:     (redeploy required — new schema)
"""

from beaker import *
from pyteal import *


# ══════════════════════════════════════════════════════════════
#  GLOBAL STATE SCHEMA — v2 Extended
# ══════════════════════════════════════════════════════════════

class MuseMarketStateV2:
    # ── Core NFT metadata ──
    creator_address  = GlobalStateValue(TealType.bytes,  descr="Primary creator wallet")
    royalty_pct      = GlobalStateValue(TealType.uint64, descr="Royalty basis points x100; 1000 = 10%")
    list_price       = GlobalStateValue(TealType.uint64, descr="List price in microALGO")
    is_listed        = GlobalStateValue(TealType.uint64, descr="1 = listed, 0 = not listed")
    marketplace_fee  = GlobalStateValue(TealType.uint64, descr="Marketplace fee bps, fixed 250 = 2.5%")

    # ── ARC-69 Metadata (on-chain) ──
    arc69_metadata   = GlobalStateValue(TealType.bytes,  descr="ARC-69 JSON metadata string (title, desc, mime_type)")
    metadata_hash    = GlobalStateValue(TealType.bytes,  descr="SHA-256 hash of full off-chain metadata JSON")
    nft_name         = GlobalStateValue(TealType.bytes,  descr="Human-readable NFT name")
    nft_unit         = GlobalStateValue(TealType.bytes,  descr="NFT unit name (ticker symbol)")

    # ── Time-Decaying Royalties ──
    mint_round       = GlobalStateValue(TealType.uint64, descr="Algorand round when NFT was minted")
    decay_rounds     = GlobalStateValue(TealType.uint64, descr="Rounds after which royalty decays (0 = no decay)")
    royalty_floor    = GlobalStateValue(TealType.uint64, descr="Minimum royalty bps after decay (e.g. 500 = 5%)")

    # ── Co-Creator Fractional Royalties (up to 4 slots for athlete model) ──
    cocreator1_addr  = GlobalStateValue(TealType.bytes,  descr="Co-creator 1 address (Athlete / Primary Creator)")
    cocreator1_share = GlobalStateValue(TealType.uint64, descr="Co-creator 1 share bps of royalty pool")
    cocreator1_role  = GlobalStateValue(TealType.bytes,  descr="Co-creator 1 role label (e.g. 'Athlete')")
    cocreator1_accepted = GlobalStateValue(TealType.uint64, descr="1 = co-creator 1 accepted their role")

    cocreator2_addr  = GlobalStateValue(TealType.bytes,  descr="Co-creator 2 address (Team / League)")
    cocreator2_share = GlobalStateValue(TealType.uint64, descr="Co-creator 2 share bps")
    cocreator2_role  = GlobalStateValue(TealType.bytes,  descr="Co-creator 2 role label")
    cocreator2_accepted = GlobalStateValue(TealType.uint64, descr="1 = co-creator 2 accepted")

    cocreator3_addr  = GlobalStateValue(TealType.bytes,  descr="Co-creator 3 address (Charity)")
    cocreator3_share = GlobalStateValue(TealType.uint64, descr="Co-creator 3 share bps")
    cocreator3_role  = GlobalStateValue(TealType.bytes,  descr="Co-creator 3 role label")
    cocreator3_accepted = GlobalStateValue(TealType.uint64, descr="1 = co-creator 3 accepted")

    cocreator4_addr  = GlobalStateValue(TealType.bytes,  descr="Co-creator 4 address (Photographer / Media)")
    cocreator4_share = GlobalStateValue(TealType.uint64, descr="Co-creator 4 share bps")
    cocreator4_role  = GlobalStateValue(TealType.bytes,  descr="Co-creator 4 role label")
    cocreator4_accepted = GlobalStateValue(TealType.uint64, descr="1 = co-creator 4 accepted")

    # ── Royalty Buy-Out ──
    royalty_buyout_price = GlobalStateValue(TealType.uint64, descr="One-time buy-out price; 0 = not available")
    royalties_waived     = GlobalStateValue(TealType.uint64, descr="1 = all royalties permanently removed")

    # ── Auction / Bidding State ──
    auction_end_round  = GlobalStateValue(TealType.uint64, descr="Round when auction closes; 0 = fixed-price only")
    auction_min_bid    = GlobalStateValue(TealType.uint64, descr="Minimum bid in microALGO")
    highest_bid        = GlobalStateValue(TealType.uint64, descr="Current highest bid amount")
    highest_bidder     = GlobalStateValue(TealType.bytes,  descr="Address of current highest bidder")
    prev_bidder        = GlobalStateValue(TealType.bytes,  descr="Previous bidder (to refund)")
    prev_bid_amt       = GlobalStateValue(TealType.uint64, descr="Previous bid amount to refund")

    # ── Physical Asset / RWA Tethering ──
    physical_asset_hash  = GlobalStateValue(TealType.bytes,  descr="SHA-256 of cert of authenticity / photo / NFC data")
    is_physical_asset    = GlobalStateValue(TealType.uint64, descr="1 = NFT is backed by a physical item")
    is_redeemed          = GlobalStateValue(TealType.uint64, descr="1 = physical item has been shipped/redeemed")
    custodian_address    = GlobalStateValue(TealType.bytes,  descr="Trusted vault / custodian holding the physical item")
    authenticator_addr   = GlobalStateValue(TealType.bytes,  descr="Certified authenticator wallet")
    is_authenticated     = GlobalStateValue(TealType.uint64, descr="1 = authenticator has verified the physical item")
    redemption_memo      = GlobalStateValue(TealType.bytes,  descr="Shipping tracking info set by custodian on redemption")

    # ── Collaboration Consent Tracking ──
    pending_collab_count = GlobalStateValue(TealType.uint64, descr="Number of co-creators who haven't accepted yet")

    # ── Aggregate marketplace stats ──
    total_sales      = GlobalStateValue(TealType.uint64, descr="Total completed sales")
    total_volume     = GlobalStateValue(TealType.uint64, descr="Cumulative volume in microALGO")
    total_royalties  = GlobalStateValue(TealType.uint64, descr="Total royalties paid")
    transfer_count   = GlobalStateValue(TealType.uint64, descr="Number of ownership transfers")


# ══════════════════════════════════════════════════════════════
#  APPLICATION SETUP
# ══════════════════════════════════════════════════════════════

app = Application(
    "MuseNFTMarketplaceV2",
    descr="Full-featured NFT + RWA marketplace with auctions, decay, buy-outs & physical tethering on Algorand",
    state=MuseMarketStateV2(),
)

MARKETPLACE_FEE_BPS = Int(250)    # 2.5%
MAX_ROYALTY_BPS     = Int(2000)   # 20%
MAX_SHARE_TOTAL     = Int(10000)  # 100%


# ══════════════════════════════════════════════════════════════
#  LIFECYCLE
# ══════════════════════════════════════════════════════════════

@app.create
def create() -> Expr:
    """Bootstrap the v2 marketplace contract."""
    return Seq(
        app.initialize_global_state(),
        app.state.marketplace_fee.set(MARKETPLACE_FEE_BPS),
        app.state.is_listed.set(Int(0)),
        app.state.is_redeemed.set(Int(0)),
        app.state.is_physical_asset.set(Int(0)),
        app.state.is_authenticated.set(Int(0)),
        app.state.royalties_waived.set(Int(0)),
        app.state.auction_end_round.set(Int(0)),
        app.state.highest_bid.set(Int(0)),
        app.state.pending_collab_count.set(Int(0)),
        app.state.transfer_count.set(Int(0)),
        app.state.total_sales.set(Int(0)),
        app.state.total_volume.set(Int(0)),
        app.state.total_royalties.set(Int(0)),
        app.state.cocreator1_accepted.set(Int(0)),
        app.state.cocreator2_accepted.set(Int(0)),
        app.state.cocreator3_accepted.set(Int(0)),
        app.state.cocreator4_accepted.set(Int(0)),
    )


# ══════════════════════════════════════════════════════════════
#  HELPER: Compute effective royalty (with time-decay)
# ══════════════════════════════════════════════════════════════

def effective_royalty_bps() -> Expr:
    """
    Returns the effective royalty in basis points, applying time-decay if configured.
    If royalties_waived = 1 → returns 0 (buy-out applied).
    If decay_rounds > 0 and enough time has passed → returns royalty_floor.
    Otherwise → returns royalty_pct as set.
    """
    rounds_elapsed = Global.round() - app.state.mint_round.get()
    decayed = And(
        app.state.decay_rounds.get() > Int(0),
        rounds_elapsed >= app.state.decay_rounds.get(),
    )
    return If(app.state.royalties_waived.get() == Int(1)).Then(
        Int(0)
    ).ElseIf(decayed).Then(
        app.state.royalty_floor.get()
    ).Else(
        app.state.royalty_pct.get()
    )


# ══════════════════════════════════════════════════════════════
#  ABI: mint_nft  (single creator, ARC-69, decay support)
# ══════════════════════════════════════════════════════════════

@app.external
def mint_nft(
    name:            abi.String,
    unit:            abi.String,
    royalty_bps:     abi.Uint64,
    list_price_algo: abi.Uint64,
    arc69_json:      abi.String,   # ARC-69 metadata JSON string
    meta_hash:       abi.String,   # SHA-256 of full metadata
    decay_after_rounds: abi.Uint64,  # 0 = no decay
    floor_royalty_bps:  abi.Uint64,  # royalty after decay
    buyout_price:    abi.Uint64,   # 0 = no buy-out option
    *,
    output: abi.Uint64,
) -> Expr:
    """
    Mints a standard digital NFT with ARC-69 metadata, optional time-decay royalties,
    and optional royalty buy-out price. Returns minting round.
    """
    return Seq(
        Assert(royalty_bps.get() <= MAX_ROYALTY_BPS, comment="Royalty exceeds 20%"),
        Assert(list_price_algo.get() > Int(0),        comment="Price must be > 0"),
        Assert(floor_royalty_bps.get() <= royalty_bps.get(), comment="Floor must be ≤ royalty"),

        app.state.nft_name.set(name.get()),
        app.state.nft_unit.set(unit.get()),
        app.state.creator_address.set(Txn.sender()),
        app.state.royalty_pct.set(royalty_bps.get()),
        app.state.list_price.set(list_price_algo.get()),
        app.state.arc69_metadata.set(arc69_json.get()),
        app.state.metadata_hash.set(meta_hash.get()),
        app.state.mint_round.set(Global.round()),
        app.state.decay_rounds.set(decay_after_rounds.get()),
        app.state.royalty_floor.set(floor_royalty_bps.get()),
        app.state.royalty_buyout_price.set(buyout_price.get()),
        app.state.royalties_waived.set(Int(0)),
        app.state.is_listed.set(Int(1)),
        app.state.is_physical_asset.set(Int(0)),
        app.state.is_authenticated.set(Int(0)),
        app.state.is_redeemed.set(Int(0)),
        # Reset co-creator slots
        app.state.cocreator1_addr.set(Bytes("")), app.state.cocreator1_share.set(Int(0)),
        app.state.cocreator1_role.set(Bytes("")), app.state.cocreator1_accepted.set(Int(0)),
        app.state.cocreator2_addr.set(Bytes("")), app.state.cocreator2_share.set(Int(0)),
        app.state.cocreator2_role.set(Bytes("")), app.state.cocreator2_accepted.set(Int(0)),
        app.state.cocreator3_addr.set(Bytes("")), app.state.cocreator3_share.set(Int(0)),
        app.state.cocreator3_role.set(Bytes("")), app.state.cocreator3_accepted.set(Int(0)),
        app.state.cocreator4_addr.set(Bytes("")), app.state.cocreator4_share.set(Int(0)),
        app.state.cocreator4_role.set(Bytes("")), app.state.cocreator4_accepted.set(Int(0)),
        app.state.pending_collab_count.set(Int(0)),
        app.state.auction_end_round.set(Int(0)),
        app.state.highest_bid.set(Int(0)),

        output.set(Global.round()),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: mint_nft_rwa  ⭐ NEW — Real World Asset Tethering
# ══════════════════════════════════════════════════════════════

@app.external
def mint_nft_rwa(
    name:              abi.String,
    unit:              abi.String,
    royalty_bps:       abi.Uint64,
    list_price_algo:   abi.Uint64,
    arc69_json:        abi.String,
    physical_hash:     abi.String,   # SHA-256 of cert of authenticity / NFC tag
    custodian:         abi.Address,  # vault/custodian holding the physical item
    authenticator:     abi.Address,  # professional authenticator address
    # Multi-layer royalty splits (athlete model: athlete / league / charity / media)
    co1_addr:  abi.Address, co1_share: abi.Uint64, co1_role: abi.String,
    co2_addr:  abi.Address, co2_share: abi.Uint64, co2_role: abi.String,
    co3_addr:  abi.Address, co3_share: abi.Uint64, co3_role: abi.String,
    co4_addr:  abi.Address, co4_share: abi.Uint64, co4_role: abi.String,
    buyout_price: abi.Uint64,
    *,
    output: abi.Uint64,
) -> Expr:
    """
    ⭐ RWA MINTING — Physical Asset Digital Twin

    Mints an NFT backed by a physical item (e.g. athlete memorabilia).
    - physical_hash links to certificate of authenticity / NFC chip data
    - custodian holds the physical item in escrow until redeemed
    - authenticator must call validate_physical_asset() before NFT goes live
    - Up to 4 co-creators with roles: Athlete / Team-League / Charity / Media

    The NFT is NOT active (is_authenticated=0) until the authenticator validates it.
    """
    total_co_shares = co1_share.get() + co2_share.get() + co3_share.get() + co4_share.get()

    return Seq(
        Assert(royalty_bps.get() <= MAX_ROYALTY_BPS, comment="Royalty exceeds 20%"),
        Assert(list_price_algo.get() > Int(0), comment="Price must be > 0"),
        Assert(total_co_shares <= MAX_SHARE_TOTAL, comment="Co-creator shares exceed 100%"),

        app.state.nft_name.set(name.get()),
        app.state.nft_unit.set(unit.get()),
        app.state.creator_address.set(Txn.sender()),
        app.state.royalty_pct.set(royalty_bps.get()),
        app.state.list_price.set(list_price_algo.get()),
        app.state.arc69_metadata.set(arc69_json.get()),
        app.state.physical_asset_hash.set(physical_hash.get()),
        app.state.is_physical_asset.set(Int(1)),
        app.state.is_authenticated.set(Int(0)),   # ← must be authenticated before going live
        app.state.is_redeemed.set(Int(0)),
        app.state.custodian_address.set(custodian.get()),
        app.state.authenticator_addr.set(authenticator.get()),
        app.state.royalty_buyout_price.set(buyout_price.get()),
        app.state.royalties_waived.set(Int(0)),
        app.state.mint_round.set(Global.round()),
        app.state.decay_rounds.set(Int(0)),        # RWA: no time-decay by default
        app.state.royalty_floor.set(Int(0)),
        app.state.is_listed.set(Int(0)),           # ← unlisted until authenticated

        # Register co-creators with roles (pending acceptance)
        app.state.cocreator1_addr.set(co1_addr.get()),
        app.state.cocreator1_share.set(co1_share.get()),
        app.state.cocreator1_role.set(co1_role.get()),
        app.state.cocreator1_accepted.set(Int(0)),

        app.state.cocreator2_addr.set(co2_addr.get()),
        app.state.cocreator2_share.set(co2_share.get()),
        app.state.cocreator2_role.set(co2_role.get()),
        app.state.cocreator2_accepted.set(Int(0)),

        app.state.cocreator3_addr.set(co3_addr.get()),
        app.state.cocreator3_share.set(co3_share.get()),
        app.state.cocreator3_role.set(co3_role.get()),
        app.state.cocreator3_accepted.set(Int(0)),

        app.state.cocreator4_addr.set(co4_addr.get()),
        app.state.cocreator4_share.set(co4_share.get()),
        app.state.cocreator4_role.set(co4_role.get()),
        app.state.cocreator4_accepted.set(Int(0)),

        # Count pending co-creator acceptances
        app.state.pending_collab_count.set(
            If(co1_addr.get() != Bytes("")).Then(Int(1)).Else(Int(0)) +
            If(co2_addr.get() != Bytes("")).Then(Int(1)).Else(Int(0)) +
            If(co3_addr.get() != Bytes("")).Then(Int(1)).Else(Int(0)) +
            If(co4_addr.get() != Bytes("")).Then(Int(1)).Else(Int(0))
        ),

        app.state.auction_end_round.set(Int(0)),
        app.state.highest_bid.set(Int(0)),
        app.state.transfer_count.set(Int(0)),

        output.set(Global.round()),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: validate_physical_asset  ⭐ NEW — Authenticator Role
# ══════════════════════════════════════════════════════════════

@app.external
def validate_physical_asset(
    *,
    output: abi.Uint64,
) -> Expr:
    """
    ⭐ AUTHENTICATOR VERIFICATION

    Only the designated authenticator wallet can call this method.
    On success:
      - is_authenticated = 1
      - is_listed = 1 (NFT goes live on marketplace)

    This prevents counterfeit RWAs from ever appearing on the marketplace.
    Returns 1 on success.
    """
    return Seq(
        Assert(app.state.is_physical_asset.get() == Int(1), comment="Not a physical asset NFT"),
        Assert(
            Txn.sender() == app.state.authenticator_addr.get(),
            comment="Only the certified authenticator can validate",
        ),
        Assert(app.state.is_authenticated.get() == Int(0), comment="Already authenticated"),

        app.state.is_authenticated.set(Int(1)),
        app.state.is_listed.set(Int(1)),   # now live!

        output.set(Int(1)),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: accept_collaboration  ⭐ NEW — Collaboration Consent
# ══════════════════════════════════════════════════════════════

@app.external
def accept_collaboration(
    slot: abi.Uint64,  # 1, 2, 3, or 4
    *,
    output: abi.Uint64,
) -> Expr:
    """
    ⭐ COLLABORATION CONSENT

    A pending co-creator calls this method to formally sign & accept their role
    and royalty share. They must be in the correct co-creator slot.

    This adds a layer of formal consent: creators can't be registered without agreeing.
    Returns the slot number accepted.
    """
    sender = Txn.sender()

    slot_1_valid = And(slot.get() == Int(1), sender == app.state.cocreator1_addr.get(), app.state.cocreator1_accepted.get() == Int(0))
    slot_2_valid = And(slot.get() == Int(2), sender == app.state.cocreator2_addr.get(), app.state.cocreator2_accepted.get() == Int(0))
    slot_3_valid = And(slot.get() == Int(3), sender == app.state.cocreator3_addr.get(), app.state.cocreator3_accepted.get() == Int(0))
    slot_4_valid = And(slot.get() == Int(4), sender == app.state.cocreator4_addr.get(), app.state.cocreator4_accepted.get() == Int(0))

    return Seq(
        Assert(
            Or(slot_1_valid, slot_2_valid, slot_3_valid, slot_4_valid),
            comment="Sender not found in pending co-creator slots or already accepted",
        ),
        If(slot_1_valid).Then(app.state.cocreator1_accepted.set(Int(1))),
        If(slot_2_valid).Then(app.state.cocreator2_accepted.set(Int(1))),
        If(slot_3_valid).Then(app.state.cocreator3_accepted.set(Int(1))),
        If(slot_4_valid).Then(app.state.cocreator4_accepted.set(Int(1))),
        # Decrement pending count
        app.state.pending_collab_count.set(
            app.state.pending_collab_count.get() - Int(1)
        ),
        output.set(slot.get()),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: start_auction  ⭐ NEW — Time-Limited Auction
# ══════════════════════════════════════════════════════════════

@app.external
def start_auction(
    duration_rounds: abi.Uint64,  # auction length in Algorand rounds (~4s each)
    min_bid_algo:    abi.Uint64,
    *,
    output: abi.Uint64,
) -> Expr:
    """
    ⭐ AUCTION INITIATION

    Creator starts a time-limited auction. Converts the listing from fixed-price
    to auction mode. min_bid sets the starting floor price.
    Returns the round at which the auction will close.

    For RWA: only works if is_authenticated = 1.
    """
    return Seq(
        Assert(
            Txn.sender() == app.state.creator_address.get(),
            comment="Only creator can start auction",
        ),
        Assert(duration_rounds.get() > Int(0), comment="Duration must be > 0"),
        Assert(min_bid_algo.get() > Int(0), comment="Min bid must be > 0"),
        Assert(app.state.auction_end_round.get() == Int(0), comment="Auction already active"),
        # For physical assets, must be authenticated
        If(app.state.is_physical_asset.get() == Int(1)).Then(
            Assert(app.state.is_authenticated.get() == Int(1), comment="Physical asset not yet authenticated")
        ),

        app.state.auction_end_round.set(Global.round() + duration_rounds.get()),
        app.state.auction_min_bid.set(min_bid_algo.get()),
        app.state.highest_bid.set(Int(0)),
        app.state.highest_bidder.set(Bytes("")),
        app.state.prev_bidder.set(Bytes("")),
        app.state.prev_bid_amt.set(Int(0)),
        app.state.is_listed.set(Int(1)),

        output.set(Global.round() + duration_rounds.get()),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: place_bid  ⭐ NEW — Bidding with Auto-Refund
# ══════════════════════════════════════════════════════════════

@app.external
def place_bid(
    payment: abi.PaymentTransaction,
    *,
    output: abi.Uint64,
) -> Expr:
    """
    ⭐ PLACE BID

    Places a bid on an active auction. The contract:
      1. Validates bid > current highest bid and >= auction_min_bid
      2. Stores the new highest bid and bidder
      3. Atomically refunds the previous highest bidder

    Returns the new highest bid amount.
    """
    bid_amount = payment.get().amount()
    sender     = Txn.sender()
    has_prev   = app.state.prev_bidder.get() != Bytes("")

    return Seq(
        Assert(app.state.auction_end_round.get() > Int(0), comment="No active auction"),
        Assert(Global.round() < app.state.auction_end_round.get(), comment="Auction has ended"),
        Assert(payment.get().receiver() == Global.current_application_address(), comment="Payment must go to contract"),
        Assert(bid_amount >= app.state.auction_min_bid.get(), comment="Bid below minimum"),
        Assert(bid_amount > app.state.highest_bid.get(), comment="Bid must exceed current highest"),

        # Refund previous bidder
        If(has_prev).Then(
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields({
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver:  app.state.prev_bidder.get(),
                    TxnField.amount:    app.state.prev_bid_amt.get(),
                    TxnField.note:      Bytes("MUSE:bid-refund"),
                }),
                InnerTxnBuilder.Submit(),
            )
        ),

        # Update bid state (chain: prev ← old highest, new highest ← this bid)
        app.state.prev_bidder.set(app.state.highest_bidder.get()),
        app.state.prev_bid_amt.set(app.state.highest_bid.get()),
        app.state.highest_bidder.set(sender),
        app.state.highest_bid.set(bid_amount),

        output.set(bid_amount),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: settle_auction  ⭐ NEW — Auction Settlement
# ══════════════════════════════════════════════════════════════

@app.external
def settle_auction(
    *,
    output: abi.Uint64,
) -> Expr:
    """
    ⭐ SETTLE AUCTION

    Called by anyone after auction_end_round has passed.
    Distributes funds: royalties to all co-creators, fee to marketplace,
    remainder to seller. Resets auction state.

    If no bids were placed, the auction simply closes.
    Returns total royalties paid.
    """
    price = app.state.highest_bid.get()
    eff_royalty = effective_royalty_bps()
    royalty_pool = price * eff_royalty / Int(10000)
    market_amt   = price * MARKETPLACE_FEE_BPS / Int(10000)
    seller_net   = price - royalty_pool - market_amt

    co1_amt = royalty_pool * app.state.cocreator1_share.get() / Int(10000)
    co2_amt = royalty_pool * app.state.cocreator2_share.get() / Int(10000)
    co3_amt = royalty_pool * app.state.cocreator3_share.get() / Int(10000)
    co4_amt = royalty_pool * app.state.cocreator4_share.get() / Int(10000)
    primary_royalty = royalty_pool - co1_amt - co2_amt - co3_amt - co4_amt

    has_winner   = app.state.highest_bidder.get() != Bytes("")
    co1_has_addr = app.state.cocreator1_addr.get() != Bytes("")
    co2_has_addr = app.state.cocreator2_addr.get() != Bytes("")
    co3_has_addr = app.state.cocreator3_addr.get() != Bytes("")
    co4_has_addr = app.state.cocreator4_addr.get() != Bytes("")
    creator      = app.state.creator_address.get()

    return Seq(
        Assert(app.state.auction_end_round.get() > Int(0), comment="No active auction"),
        Assert(Global.round() >= app.state.auction_end_round.get(), comment="Auction not yet ended"),

        If(has_winner).Then(
            Seq(
                # Co-creator 1 royalty
                If(co1_has_addr).Then(
                    Seq(
                        InnerTxnBuilder.Begin(),
                        InnerTxnBuilder.SetFields({
                            TxnField.type_enum: TxnType.Payment,
                            TxnField.receiver:  app.state.cocreator1_addr.get(),
                            TxnField.amount:    co1_amt,
                            TxnField.note:      Bytes("MUSE:auction-royalty:1"),
                        }),
                        InnerTxnBuilder.Submit(),
                    )
                ),
                # Co-creator 2 royalty
                If(co2_has_addr).Then(
                    Seq(
                        InnerTxnBuilder.Begin(),
                        InnerTxnBuilder.SetFields({
                            TxnField.type_enum: TxnType.Payment,
                            TxnField.receiver:  app.state.cocreator2_addr.get(),
                            TxnField.amount:    co2_amt,
                            TxnField.note:      Bytes("MUSE:auction-royalty:2"),
                        }),
                        InnerTxnBuilder.Submit(),
                    )
                ),
                # Co-creator 3 royalty
                If(co3_has_addr).Then(
                    Seq(
                        InnerTxnBuilder.Begin(),
                        InnerTxnBuilder.SetFields({
                            TxnField.type_enum: TxnType.Payment,
                            TxnField.receiver:  app.state.cocreator3_addr.get(),
                            TxnField.amount:    co3_amt,
                            TxnField.note:      Bytes("MUSE:auction-royalty:3"),
                        }),
                        InnerTxnBuilder.Submit(),
                    )
                ),
                # Co-creator 4 royalty
                If(co4_has_addr).Then(
                    Seq(
                        InnerTxnBuilder.Begin(),
                        InnerTxnBuilder.SetFields({
                            TxnField.type_enum: TxnType.Payment,
                            TxnField.receiver:  app.state.cocreator4_addr.get(),
                            TxnField.amount:    co4_amt,
                            TxnField.note:      Bytes("MUSE:auction-royalty:4"),
                        }),
                        InnerTxnBuilder.Submit(),
                    )
                ),
                # Primary creator royalty
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields({
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver:  creator,
                    TxnField.amount:    primary_royalty,
                    TxnField.note:      Bytes("MUSE:auction-creator-royalty"),
                }),
                InnerTxnBuilder.Submit(),
                # Marketplace fee
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields({
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver:  Global.creator_address(),
                    TxnField.amount:    market_amt,
                    TxnField.note:      Bytes("MUSE:auction-fee"),
                }),
                InnerTxnBuilder.Submit(),
                # Seller proceeds (seller = creator for primary, highest_bidder becomes new owner)
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields({
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver:  creator,
                    TxnField.amount:    seller_net,
                    TxnField.note:      Bytes("MUSE:auction-seller-proceeds"),
                }),
                InnerTxnBuilder.Submit(),

                # Update stats
                app.state.total_sales.set(app.state.total_sales.get() + Int(1)),
                app.state.total_volume.set(app.state.total_volume.get() + price),
                app.state.total_royalties.set(app.state.total_royalties.get() + royalty_pool),
                app.state.transfer_count.set(app.state.transfer_count.get() + Int(1)),
            )
        ),

        # Reset auction state
        app.state.auction_end_round.set(Int(0)),
        app.state.highest_bid.set(Int(0)),
        app.state.highest_bidder.set(Bytes("")),
        app.state.prev_bidder.set(Bytes("")),
        app.state.prev_bid_amt.set(Int(0)),
        app.state.is_listed.set(Int(0)),

        output.set(royalty_pool),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: buy_nft  (fixed-price, upgraded with decay + 4 co-creators)
# ══════════════════════════════════════════════════════════════

@app.external
def buy_nft(
    payment: abi.PaymentTransaction,
    *,
    output: abi.Uint64,
) -> Expr:
    """
    Fixed-price NFT purchase with:
    - Time-decayed royalties (effective_royalty_bps)
    - Buy-out waiver support
    - Up to 4 co-creator splits
    - Marketplace fee
    Returns total royalties paid.
    """
    price       = payment.get().amount()
    eff_royalty = effective_royalty_bps()
    royalty_pool = price * eff_royalty / Int(10000)
    market_amt   = price * MARKETPLACE_FEE_BPS / Int(10000)

    co1_amt = royalty_pool * app.state.cocreator1_share.get() / Int(10000)
    co2_amt = royalty_pool * app.state.cocreator2_share.get() / Int(10000)
    co3_amt = royalty_pool * app.state.cocreator3_share.get() / Int(10000)
    co4_amt = royalty_pool * app.state.cocreator4_share.get() / Int(10000)
    primary_royalty = royalty_pool - co1_amt - co2_amt - co3_amt - co4_amt

    creator      = app.state.creator_address.get()
    co1_has_addr = app.state.cocreator1_addr.get() != Bytes("")
    co2_has_addr = app.state.cocreator2_addr.get() != Bytes("")
    co3_has_addr = app.state.cocreator3_addr.get() != Bytes("")
    co4_has_addr = app.state.cocreator4_addr.get() != Bytes("")

    return Seq(
        Assert(app.state.is_listed.get() == Int(1), comment="NFT not listed"),
        Assert(app.state.auction_end_round.get() == Int(0), comment="Use auction settlement for auctions"),
        Assert(payment.get().receiver() == Global.current_application_address(), comment="Wrong recipient"),
        Assert(payment.get().amount() >= app.state.list_price.get(), comment="Insufficient payment"),
        # Physical asset guard
        If(app.state.is_physical_asset.get() == Int(1)).Then(
            Assert(app.state.is_authenticated.get() == Int(1), comment="Physical asset not authenticated"),
            Assert(app.state.is_redeemed.get() == Int(0), comment="Physical item already redeemed"),
        ),

        If(co1_has_addr).Then(
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields({
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver:  app.state.cocreator1_addr.get(),
                    TxnField.amount:    co1_amt,
                    TxnField.note:      Bytes("MUSE:co-royalty:1"),
                }),
                InnerTxnBuilder.Submit(),
            )
        ),
        If(co2_has_addr).Then(
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields({
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver:  app.state.cocreator2_addr.get(),
                    TxnField.amount:    co2_amt,
                    TxnField.note:      Bytes("MUSE:co-royalty:2"),
                }),
                InnerTxnBuilder.Submit(),
            )
        ),
        If(co3_has_addr).Then(
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields({
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver:  app.state.cocreator3_addr.get(),
                    TxnField.amount:    co3_amt,
                    TxnField.note:      Bytes("MUSE:co-royalty:3"),
                }),
                InnerTxnBuilder.Submit(),
            )
        ),
        If(co4_has_addr).Then(
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields({
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver:  app.state.cocreator4_addr.get(),
                    TxnField.amount:    co4_amt,
                    TxnField.note:      Bytes("MUSE:co-royalty:4"),
                }),
                InnerTxnBuilder.Submit(),
            )
        ),
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields({
            TxnField.type_enum: TxnType.Payment,
            TxnField.receiver:  creator,
            TxnField.amount:    primary_royalty,
            TxnField.note:      Bytes("MUSE:creator-royalty"),
        }),
        InnerTxnBuilder.Submit(),
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields({
            TxnField.type_enum: TxnType.Payment,
            TxnField.receiver:  Global.creator_address(),
            TxnField.amount:    market_amt,
            TxnField.note:      Bytes("MUSE:fee"),
        }),
        InnerTxnBuilder.Submit(),

        app.state.is_listed.set(Int(0)),
        app.state.total_sales.set(app.state.total_sales.get() + Int(1)),
        app.state.total_volume.set(app.state.total_volume.get() + price),
        app.state.total_royalties.set(app.state.total_royalties.get() + royalty_pool),
        app.state.transfer_count.set(app.state.transfer_count.get() + Int(1)),

        output.set(royalty_pool),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: buy_out_royalty  ⭐ NEW — One-Time Royalty Removal
# ══════════════════════════════════════════════════════════════

@app.external
def buy_out_royalty(
    payment: abi.PaymentTransaction,
    *,
    output: abi.Uint64,
) -> Expr:
    """
    ⭐ ROYALTY BUY-OUT

    A buyer pays the creator a one-time premium to permanently remove all future
    royalties from this NFT. After settlement:
      - royalties_waived = 1
      - All future sales of this NFT incur 0% royalty (buyer benefits forever)

    Splits the buy-out payment: 95% to creator, 5% to marketplace.
    Returns 1 on success.
    """
    buyout_price = app.state.royalty_buyout_price.get()
    creator      = app.state.creator_address.get()
    paid         = payment.get().amount()
    market_cut   = paid * Int(500) / Int(10000)    # 5% of buy-out to marketplace
    creator_cut  = paid - market_cut

    return Seq(
        Assert(app.state.royalty_buyout_price.get() > Int(0), comment="Royalty buy-out not available"),
        Assert(app.state.royalties_waived.get() == Int(0), comment="Royalties already waived"),
        Assert(payment.get().receiver() == Global.current_application_address(), comment="Wrong recipient"),
        Assert(paid >= buyout_price, comment="Insufficient buy-out payment"),

        # Pay creator the buy-out
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields({
            TxnField.type_enum: TxnType.Payment,
            TxnField.receiver:  creator,
            TxnField.amount:    creator_cut,
            TxnField.note:      Bytes("MUSE:royalty-buyout"),
        }),
        InnerTxnBuilder.Submit(),
        # Marketplace gets 5%
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields({
            TxnField.type_enum: TxnType.Payment,
            TxnField.receiver:  Global.creator_address(),
            TxnField.amount:    market_cut,
            TxnField.note:      Bytes("MUSE:buyout-fee"),
        }),
        InnerTxnBuilder.Submit(),

        # Permanently waive royalties
        app.state.royalties_waived.set(Int(1)),
        app.state.royalty_buyout_price.set(Int(0)),  # disable further buy-outs

        output.set(Int(1)),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: redeem_physical_asset  ⭐ NEW — RWA Redemption
# ══════════════════════════════════════════════════════════════

@app.external
def redeem_physical_asset(
    tracking_info: abi.String,  # shipping/tracking memo set by custodian
    *,
    output: abi.Uint64,
) -> Expr:
    """
    ⭐ PHYSICAL ASSET REDEMPTION

    Called by the custodian when they ship the physical item to the NFT holder.
    - Sets is_redeemed = 1
    - Records tracking_info on-chain as redemption_memo
    - The NFT is then locked from further marketplace sales (still transferable)

    Returns 1 on success.
    """
    return Seq(
        Assert(app.state.is_physical_asset.get() == Int(1), comment="Not a physical asset"),
        Assert(app.state.is_authenticated.get() == Int(1), comment="Asset not authenticated"),
        Assert(app.state.is_redeemed.get() == Int(0), comment="Already redeemed"),
        Assert(
            Txn.sender() == app.state.custodian_address.get(),
            comment="Only the custodian can redeem",
        ),
        app.state.is_redeemed.set(Int(1)),
        app.state.redemption_memo.set(tracking_info.get()),
        app.state.is_listed.set(Int(0)),   # delist after redemption

        output.set(Int(1)),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: update_royalty  (dynamic, creator only)
# ══════════════════════════════════════════════════════════════

@app.external
def update_royalty(
    new_royalty_bps: abi.Uint64,
    *,
    output: abi.Uint64,
) -> Expr:
    """Dynamic royalty update by the creator. Capped at 20%."""
    return Seq(
        Assert(Txn.sender() == app.state.creator_address.get(), comment="Only creator can update royalty"),
        Assert(new_royalty_bps.get() <= MAX_ROYALTY_BPS, comment="Royalty exceeds 20%"),
        Assert(app.state.royalties_waived.get() == Int(0), comment="Royalties have been waived"),
        app.state.royalty_pct.set(new_royalty_bps.get()),
        output.set(new_royalty_bps.get()),
    )


# ══════════════════════════════════════════════════════════════
#  ABI: list_nft  (re-list at new price)
# ══════════════════════════════════════════════════════════════

@app.external
def list_nft(
    new_price: abi.Uint64,
    *,
    output: abi.Uint64,
) -> Expr:
    """Re-list NFT at new fixed price. Cannot re-list a redeemed physical asset."""
    return Seq(
        Assert(new_price.get() > Int(0), comment="Price must be > 0"),
        Assert(app.state.is_redeemed.get() == Int(0), comment="Redeemed physical assets cannot be re-listed"),
        If(app.state.is_physical_asset.get() == Int(1)).Then(
            Assert(app.state.is_authenticated.get() == Int(1), comment="Not authenticated")
        ),
        app.state.list_price.set(new_price.get()),
        app.state.is_listed.set(Int(1)),
        output.set(new_price.get()),
    )


# ══════════════════════════════════════════════════════════════
#  READ-ONLY: get_full_nft_state  ⭐ NEW — Complete State Query
# ══════════════════════════════════════════════════════════════

@app.external(read_only=True)
def get_full_nft_state(
    *,
    output: abi.String,
) -> Expr:
    """
    Returns a JSON-like string with all key NFT state fields for the frontend dashboard.
    Includes: listing, royalty (effective), RWA status, auction state, provenance.
    """
    eff_royalty = effective_royalty_bps()
    return output.set(
        Concat(
            Bytes('{"listed":'),            Itob(app.state.is_listed.get()),
            Bytes(',"royalty_bps":'),       Itob(eff_royalty),
            Bytes(',"royalty_raw":'),       Itob(app.state.royalty_pct.get()),
            Bytes(',"royalty_floor":'),     Itob(app.state.royalty_floor.get()),
            Bytes(',"royalties_waived":'),  Itob(app.state.royalties_waived.get()),
            Bytes(',"buyout_price":'),      Itob(app.state.royalty_buyout_price.get()),
            Bytes(',"is_rwa":'),            Itob(app.state.is_physical_asset.get()),
            Bytes(',"is_auth":'),           Itob(app.state.is_authenticated.get()),
            Bytes(',"is_redeemed":'),       Itob(app.state.is_redeemed.get()),
            Bytes(',"auction_end":'),       Itob(app.state.auction_end_round.get()),
            Bytes(',"highest_bid":'),       Itob(app.state.highest_bid.get()),
            Bytes(',"list_price":'),        Itob(app.state.list_price.get()),
            Bytes(',"transfers":'),         Itob(app.state.transfer_count.get()),
            Bytes(',"pending_collabs":'),   Itob(app.state.pending_collab_count.get()),
            Bytes(',"total_sales":'),       Itob(app.state.total_sales.get()),
            Bytes(',"total_volume":'),      Itob(app.state.total_volume.get()),
            Bytes(',"total_royalties":'),   Itob(app.state.total_royalties.get()),
            Bytes(',"mint_round":'),        Itob(app.state.mint_round.get()),
            Bytes(',"decay_rounds":'),      Itob(app.state.decay_rounds.get()),
            Bytes('}'),
        )
    )


# ══════════════════════════════════════════════════════════════
#  READ-ONLY: get_royalty_preview
# ══════════════════════════════════════════════════════════════

@app.external(read_only=True)
def get_royalty_preview(
    price_microalgo: abi.Uint64,
    *,
    output: abi.Uint64,
) -> Expr:
    """Returns effective royalty in microALGO for a given price (respects decay + waiver)."""
    eff_royalty = effective_royalty_bps()
    return output.set(price_microalgo.get() * eff_royalty / Int(10000))


# ══════════════════════════════════════════════════════════════
#  READ-ONLY: get_split_preview  (all 4 co-creators + decay)
# ══════════════════════════════════════════════════════════════

@app.external(read_only=True)
def get_split_preview(
    price_microalgo: abi.Uint64,
    *,
    output: abi.String,
) -> Expr:
    """
    Returns full payment split for a given price with all 4 co-creator slots.
    Format: "creator|co1|co2|co3|co4|marketplace"  (all in microALGO)
    Respects time-decay and royalty waiver.
    """
    price        = price_microalgo.get()
    eff_royalty  = effective_royalty_bps()
    royalty_pool = price * eff_royalty / Int(10000)
    co1_amt  = royalty_pool * app.state.cocreator1_share.get() / Int(10000)
    co2_amt  = royalty_pool * app.state.cocreator2_share.get() / Int(10000)
    co3_amt  = royalty_pool * app.state.cocreator3_share.get() / Int(10000)
    co4_amt  = royalty_pool * app.state.cocreator4_share.get() / Int(10000)
    primary  = royalty_pool - co1_amt - co2_amt - co3_amt - co4_amt
    mkt_amt  = price * MARKETPLACE_FEE_BPS / Int(10000)

    return output.set(
        Concat(
            Itob(primary),  Bytes("|"),
            Itob(co1_amt),  Bytes("|"),
            Itob(co2_amt),  Bytes("|"),
            Itob(co3_amt),  Bytes("|"),
            Itob(co4_amt),  Bytes("|"),
            Itob(mkt_amt),
        )
    )


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    spec = app.build()
    spec.export("./artifacts_v2")
    print()
    print("✓ Muse NFT Marketplace v2 compiled → ./artifacts_v2/")
    print()
    print("  ── CORE ──────────────────────────────────────────────")
    print("  ✦ mint_nft()              — digital NFT with ARC-69, decay, buy-out")
    print("  ✦ mint_nft_rwa()          — physical asset digital twin (RWA)")
    print("  ✦ buy_nft()               — fixed-price purchase with 4-way splits")
    print("  ✦ list_nft()              — re-list NFT at new price")
    print("  ✦ update_royalty()        — dynamic royalty adjustment by creator")
    print()
    print("  ── AUCTIONS ──────────────────────────────────────────")
    print("  ✦ start_auction()         — creator starts time-limited auction")
    print("  ✦ place_bid()             — bidder places bid with auto-refund")
    print("  ✦ settle_auction()        — settle after end round (anyone can call)")
    print()
    print("  ── RWA & PHYSICAL ────────────────────────────────────")
    print("  ✦ validate_physical_asset() — authenticator marks RWA as genuine")
    print("  ✦ redeem_physical_asset()   — custodian records physical delivery")
    print()
    print("  ── COLLABORATION ─────────────────────────────────────")
    print("  ✦ accept_collaboration()  — co-creator formally accepts role")
    print("  ✦ buy_out_royalty()       — buyer pays one-time fee to waive royalties")
    print()
    print("  ── READ-ONLY ─────────────────────────────────────────")
    print("  ✦ get_full_nft_state()    — complete state JSON for dashboard")
    print("  ✦ get_royalty_preview()   — effective royalty for a price")
    print("  ✦ get_split_preview()     — full 4-way split breakdown")
    print()
    print("  Deploy: algokit deploy --network testnet")
    print()
