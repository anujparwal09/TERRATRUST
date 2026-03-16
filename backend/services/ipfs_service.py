"""
IPFS service — upload JSON metadata and binary files to Pinata.

Pinata is used instead of NFT.Storage (deprecated).
"""

import logging
from typing import Any, Dict

import httpx

from app.config import settings

logger = logging.getLogger("terratrust.ipfs")


async def upload_to_ipfs(metadata: Dict[str, Any], audit_id: str = "") -> str:
    """Pin a JSON object to IPFS via Pinata.

    Parameters
    ----------
    metadata : dict
        Arbitrary JSON-serialisable metadata to pin.
    audit_id : str, optional
        Used for the ``pinataMetadata.name`` field.

    Returns
    -------
    str
        IPFS URI in the form ``ipfs://<CID>``.
    """
    url = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
    headers = {
        "Authorization": f"Bearer {settings.PINATA_JWT}",
        "Content-Type": "application/json",
    }
    body = {
        "pinataContent": metadata,
        "pinataMetadata": {"name": f"terratrust-audit-{audit_id}"},
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    cid = data["IpfsHash"]
    ipfs_url = f"ipfs://{cid}"
    logger.info("Pinned metadata to IPFS: %s", ipfs_url)
    return ipfs_url


async def upload_file_to_ipfs(file_bytes: bytes, filename: str) -> str:
    """Pin a binary file to IPFS via Pinata.

    Parameters
    ----------
    file_bytes : bytes
        Raw file content.
    filename : str
        Human-readable filename for Pinata metadata.

    Returns
    -------
    str
        IPFS URI in the form ``ipfs://<CID>``.
    """
    url = "https://api.pinata.cloud/pinning/pinFileToIPFS"
    headers = {
        "Authorization": f"Bearer {settings.PINATA_JWT}",
    }

    files = {
        "file": (filename, file_bytes),
    }
    data = {
        "pinataMetadata": '{"name": "' + filename + '"}',
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, files=files, data=data, headers=headers)
        resp.raise_for_status()
        resp_data = resp.json()

    cid = resp_data["IpfsHash"]
    ipfs_url = f"ipfs://{cid}"
    logger.info("Pinned file '%s' to IPFS: %s", filename, ipfs_url)
    return ipfs_url
