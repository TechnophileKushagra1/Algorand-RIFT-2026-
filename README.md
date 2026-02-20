# Muse v2 — NFT + RWA Marketplace on Algorand

Python smart contract + deployment scripts for the **Muse v2** NFT marketplace.

---

## Features

| Feature | How it works |
|---|---|
| **ARC-69 NFT Minting** | Creates Algorand Standard Assets (ASAs) with on-chain metadata hash |
| **4-Way Royalty Splits** | Athlete / League / Charity / Media — enforced by contract |
| **Time-Decaying Royalties** | Royalty % decreases linearly over configurable months → floor % |
| **Royalty Buy-Out** | One-time ALGO payment permanently waives all future royalties |
| **RWA Physical Tethering** | SHA-256 of CoA links token to physical item; frozen until authenticated |
| **Authenticator Lifecycle** | Pending → Authenticated → Listed → Sold → Redeemed state machine |
| **English Auction** | Auto-refunds previous bidder; anti-sniping 5-min extension |
| **Co-Creator Registry** | Up to 4 co-creators must call `accept_collaboration()` on-chain |

---

## Project Structure

```
muse_v2/
├── contracts/
│   └── muse_marketplace.py   ← PyTeal/Beaker smart contract
├── deploy_and_interact.py    ← CLI deployment & interaction script
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure your wallet

Generate or import your Algorand testnet account:

```bash
# Set your mnemonic (25-word phrase from Pera/Defly wallet)
export MUSE_MNEMONIC="word1 word2 word3 ... word25"

# Set Muse treasury address (receives 2.5% platform fee)
export MUSE_TREASURY="YOUR_TREASURY_ALGO_ADDRESS"
```

Fund your testnet account at: https://testnet.algoexplorer.io/dispenser

### 3. Deploy the contract

```bash
python deploy_and_interact.py deploy
# Output: App ID: 12345678
export MUSE_APP_ID=12345678
```

---

## CLI Usage

### Mint a digital NFT

```bash
python deploy_and_interact.py mint \
  --name "Solstice Bloom #07" \
  --unit SLST7 \
  --url "ipfs://QmABC.../metadata.json" \
  --price 24 \
  --royalty 10 \
  --floor 5 \
  --decay-months 12 \
  --buyout 500
```

**Parameters:**

| Flag | Description | Example |
|---|---|---|
| `--name` | NFT display name | `"Solstice Bloom #07"` |
| `--unit` | Token symbol (max 8 chars) | `SLST7` |
| `--url` | ARC-69 metadata URL | `ipfs://Qm...` |
| `--price` | List price in ALGO (0 = auction only) | `24` |
| `--royalty` | Royalty % (max 20) | `10` |
| `--floor` | Floor royalty % after full decay | `5` |
| `--decay-months` | Months until decay completes (0 = off) | `12` |
| `--buyout` | One-time buy-out price in ALGO (0 = off) | `500` |

---

### Mint a Real World Asset (RWA)

```bash
python deploy_and_interact.py mint-rwa \
  --name "LeBron Game-Worn Jersey #23" \
  --unit FG7J \
  --price 4200 \
  --royalty 12 \
  --floor 8 \
  --phash "sha256hexhashofcertificate..." \
  --custodian "VAULT_ALGO_ADDRESS" \
  --authenticator "AUTH_ALGO_ADDRESS"
```

The token starts **frozen** (inactive) until the authenticator calls `auth-rwa`.

---

### Register Co-Creators (Royalty Splits)

```bash
python deploy_and_interact.py co-creators \
  --asset 12345678 \
  --splits '[
    {"address":"ATHLETE_ADDR","share_bps":5000,"role":"Athlete"},
    {"address":"LEAGUE_ADDR", "share_bps":2500,"role":"NBA League"},
    {"address":"CHARITY_ADDR","share_bps":1500,"role":"Charity"},
    {"address":"MEDIA_ADDR",  "share_bps":1000,"role":"Photographer"}
  ]'
```

`share_bps` is basis points of the total royalty (5000 = 50% of royalty goes here).

### Accept a Co-Creator Invitation

```bash
# Run as the co-creator (with their MUSE_MNEMONIC)
python deploy_and_interact.py accept --asset 12345678
```

---

### Buy an NFT

```bash
python deploy_and_interact.py buy --asset 12345678 --price 24
```

Royalties are automatically distributed to all split recipients in the same transaction.

---

### Royalty Buy-Out

```bash
python deploy_and_interact.py buyout --asset 12345678 --amount 500
```

Creator receives 95%, Muse treasury receives 5%. Royalty permanently waived.

---

### Auctions

```bash
# Start a 48-hour auction with 100 ALGO reserve
python deploy_and_interact.py auction \
  --asset 12345678 \
  --duration 48 \
  --reserve 100

# Bid
python deploy_and_interact.py bid --asset 12345678 --amount 150

# Settle after auction ends
python deploy_and_interact.py settle --asset 12345678
```

---

### RWA Lifecycle

```bash
# Authenticator validates the physical item
python deploy_and_interact.py auth-rwa --asset 12345678

# Custodian records physical redemption
python deploy_and_interact.py redeem-rwa \
  --asset 12345678 \
  --tracking "FedEx-1234567890"
```

---

### Query Info

```bash
# NFT details from on-chain box
python deploy_and_interact.py info --asset 12345678

# Platform-wide stats
python deploy_and_interact.py stats
```

---

## Key Constants

| Constant | Value | Meaning |
|---|---|---|
| `MUSE_FEE_BPS` | 250 | 2.5% platform fee |
| `MAX_ROYALTY_BPS` | 2000 | Max 20% royalty |
| `ROUNDS_PER_MONTH` | 777,600 | ~4s/round × 30 days |
| `ANTI_SNIPE_ROUNDS` | 75 | ~5 min auction extension |

---

## On-Chain Box Storage Layout

### NFT Box (`NFT<asset_id>`)

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | `asset_id` |
| 8 | 8 | `price` (microAlgos) |
| 16 | 8 | `royalty_bps` |
| 24 | 8 | `floor_bps` |
| 32 | 8 | `decay_start_round` |
| 40 | 8 | `buyout_price` |
| 48 | 8 | `transfers` |
| 56 | 8 | `flags` (bit field) |
| 64 | 32 | `creator` address |

### Flags Bitmask

| Bit | Name | Meaning |
|---|---|---|
| 0x01 | `IS_RWA` | Physical asset token |
| 0x02 | `RWA_AUTHENTICATED` | Authenticator approved |
| 0x04 | `RWA_REDEEMED` | Physical item claimed |
| 0x08 | `ROYALTY_WAIVED` | Buy-out executed |
| 0x10 | `IN_AUCTION` | Currently in auction |
| 0x20 | `COLLAB_PENDING` | Awaiting co-creator accepts |

---

## Testnet Resources

- Faucet: https://testnet.algoexplorer.io/dispenser
- Explorer: https://testnet.algoexplorer.io
- AlgoNode API: https://testnet-api.algonode.cloud
