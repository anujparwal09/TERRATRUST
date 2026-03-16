"""
Upload router — document upload with blockchain hash storage.

POST /upload         - Upload a document → hash → store on blockchain
POST /verify         - Verify a document hash exists on blockchain
GET  /blockchain     - Check blockchain connection status
"""

import logging

from fastapi import APIRouter, UploadFile, HTTPException, status

from app.utils.hash import generate_hash
from app.services.blockchain_service import (
    store_document_hash,
    verify_document_hash,
    get_blockchain_status,
)

logger = logging.getLogger("terratrust.upload")

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /upload — Upload document and store hash on blockchain
# ---------------------------------------------------------------------------
@router.post("/upload")
async def upload_document(file: UploadFile):
    """Upload a document, generate SHA256 hash, and store it on the Polygon blockchain.

    Workflow:
        1. Read uploaded file bytes
        2. Generate SHA256 hash
        3. Send hash to TerraToken smart contract on Polygon Amoy
        4. Return document hash + transaction hash

    Parameters
    ----------
    file : UploadFile
        The document file (PDF, image, etc.) to hash and register.

    Returns
    -------
    dict
        ``{document_hash, transaction_hash, filename}``
    """
    try:
        file_bytes = await file.read()

        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        # Generate SHA256 hash
        doc_hash = generate_hash(file_bytes)

        # Store hash on blockchain
        tx_hash = store_document_hash(doc_hash)

        logger.info(
            "Document '%s' uploaded — hash=%s, tx=%s",
            file.filename,
            doc_hash[:16] + "...",
            tx_hash,
        )

        return {
            "document_hash": doc_hash,
            "transaction_hash": tx_hash,
            "filename": file.filename,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Document upload failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Blockchain transaction failed: {str(exc)}",
        ) from exc


# ---------------------------------------------------------------------------
# POST /verify — Verify a document hash on blockchain
# ---------------------------------------------------------------------------
@router.post("/verify")
async def verify_document(file: UploadFile):
    """Verify if a document's hash exists on the blockchain.

    Upload a file → system generates its SHA256 hash → checks if that
    hash was previously stored on the Polygon blockchain.

    Parameters
    ----------
    file : UploadFile
        The document file to verify.

    Returns
    -------
    dict
        ``{verified, document_hash, audit_id, evidence_hash}``
    """
    try:
        file_bytes = await file.read()

        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        doc_hash = generate_hash(file_bytes)

        result = verify_document_hash(doc_hash)

        logger.info(
            "Document verification — hash=%s, verified=%s",
            doc_hash[:16] + "...",
            result["verified"],
        )

        return {
            "document_hash": doc_hash,
            "filename": file.filename,
            **result,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Document verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Verification failed: {str(exc)}",
        ) from exc


# ---------------------------------------------------------------------------
# GET /blockchain — Check blockchain connection status
# ---------------------------------------------------------------------------
@router.get("/blockchain")
async def blockchain_status():
    """Check if the backend is connected to the Polygon blockchain.

    Returns
    -------
    dict
        Connection status, network, chain ID, contract address,
        admin wallet address, and MATIC balance.
    """
    try:
        status_info = get_blockchain_status()
        return status_info
    except Exception as exc:
        logger.error("Blockchain status check failed: %s", exc)
        return {
            "connected": False,
            "error": str(exc),
        }
