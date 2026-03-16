"""
Blockchain service — connect FastAPI backend to TerraToken smart contract
on Polygon Amoy Testnet.

Architecture:
    User Uploads PDF
        ↓
    FastAPI Backend
        ↓
    Generate SHA256 Hash
        ↓
    Call Smart Contract (store hash via mintAudit)
        ↓
    Transaction sent to Polygon Amoy
        ↓
    Store txHash + document info in DB

Deployed contract: 0x7C2e75720dF303B0459BccC9e56130Ed05a84Ac8
"""

import json
import logging
import os

from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("terratrust.blockchain_service")

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
RPC_URL = os.getenv("ALCHEMY_POLYGON_AMOY_URL")
PRIVATE_KEY = os.getenv("ADMIN_WALLET_PRIVATE_KEY")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")

# ---------------------------------------------------------------------------
# Web3 connection
# ---------------------------------------------------------------------------
w3 = Web3(Web3.HTTPProvider(RPC_URL))

account = w3.eth.account.from_key(PRIVATE_KEY)

# ---------------------------------------------------------------------------
# Load contract ABI
# ---------------------------------------------------------------------------
ABI_PATH = os.path.join(os.path.dirname(__file__), "..", "blockchain", "terra_abi.json")

with open(ABI_PATH) as f:
    ABI = json.load(f)

contract = w3.eth.contract(
    address=Web3.to_checksum_address(CONTRACT_ADDRESS),
    abi=ABI,
)


# ---------------------------------------------------------------------------
# Store document hash on blockchain
# ---------------------------------------------------------------------------
def store_document_hash(doc_hash: str) -> str:
    """Store a document SHA256 hash on the Polygon blockchain.

    Calls the TerraToken contract's ``mintAudit`` function to create
    an immutable on-chain record of the document hash.

    Parameters
    ----------
    doc_hash : str
        SHA256 hex digest of the document.

    Returns
    -------
    str
        Transaction hash (hex string).
    """
    nonce = w3.eth.get_transaction_count(account.address)

    # Use mintAudit to store the document hash on-chain.
    # farmer = admin address (self), auditId = hash-derived int,
    # creditAmount = 0 (document storage only, no credits),
    # landId = doc_hash, auditYear = 0, ipfsHash = doc_hash
    audit_id_int = int(doc_hash[:8], 16)  # First 8 hex chars → unique int

    tx = contract.functions.mintAudit(
        account.address,       # farmer (admin stores for now)
        audit_id_int,          # auditId (derived from hash)
        0,                     # creditAmount (0 for doc storage)
        doc_hash,              # landId (using hash as identifier)
        0,                     # auditYear (0 for doc storage)
        doc_hash,              # ipfsHash (the document hash itself)
    ).build_transaction({
        "chainId": 80002,      # Polygon Amoy Testnet
        "gas": 300000,
        "gasPrice": w3.eth.gas_price,
        "nonce": nonce,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)

    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    logger.info("Document hash stored on blockchain — tx: %s", w3.to_hex(tx_hash))

    return w3.to_hex(tx_hash)


# ---------------------------------------------------------------------------
# Verify document hash on blockchain
# ---------------------------------------------------------------------------
def verify_document_hash(doc_hash: str) -> dict:
    """Verify if a document hash exists on the blockchain.

    Parameters
    ----------
    doc_hash : str
        SHA256 hex digest of the document to verify.

    Returns
    -------
    dict
        ``{verified, audit_id, evidence_hash}``
    """
    audit_id_int = int(doc_hash[:8], 16)

    try:
        evidence = contract.functions.getAuditEvidence(audit_id_int).call()
        if evidence and evidence == doc_hash:
            logger.info("Document hash VERIFIED on blockchain — auditId: %d", audit_id_int)
            return {
                "verified": True,
                "audit_id": audit_id_int,
                "evidence_hash": evidence,
            }
        else:
            return {
                "verified": False,
                "audit_id": audit_id_int,
                "evidence_hash": evidence or None,
            }
    except Exception as exc:
        logger.warning("Verification failed: %s", exc)
        return {
            "verified": False,
            "audit_id": audit_id_int,
            "evidence_hash": None,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Get blockchain connection status
# ---------------------------------------------------------------------------
def get_blockchain_status() -> dict:
    """Check if the backend is connected to Polygon.

    Returns
    -------
    dict
        ``{connected, network, chain_id, contract_address, admin_address, balance_matic}``
    """
    connected = w3.is_connected()

    if connected:
        chain_id = w3.eth.chain_id
        balance_wei = w3.eth.get_balance(account.address)
        balance_matic = float(w3.from_wei(balance_wei, "ether"))
    else:
        chain_id = None
        balance_matic = 0.0

    return {
        "connected": connected,
        "network": "Polygon Amoy Testnet" if connected else "Disconnected",
        "chain_id": chain_id,
        "contract_address": CONTRACT_ADDRESS,
        "admin_address": account.address,
        "balance_matic": round(balance_matic, 6),
    }
