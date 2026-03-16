// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title  TerraToken
 * @notice ERC-1155 contract for TerraTrust-AR carbon credits.
 *
 *         Token ID 1       = Fungible carbon credits (CTT).
 *         Token ID >= 1000 = Non-fungible audit certificates.
 *
 *         Double-mint prevention uses keccak256(landId, auditYear).
 */
contract TerraToken is ERC1155, Ownable {
    // ---------------------------------------------------------------
    // Constants
    // ---------------------------------------------------------------
    uint256 public constant CARBON_CREDIT = 1;

    // ---------------------------------------------------------------
    // State
    // ---------------------------------------------------------------

    /// @notice IPFS evidence hash for each audit certificate tokenId
    mapping(uint256 => string) public auditEvidence;

    /// @notice Tracks whether a (landId, auditYear) combo has been minted
    mapping(bytes32 => bool) public auditMinted;

    /// @notice Retired (burned) credit balance per address
    mapping(address => uint256) public retiredCredits;

    // ---------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------
    event CreditRetired(
        address indexed account,
        uint256 amount,
        uint256 timestamp
    );

    event AuditMinted(
        address indexed farmer,
        uint256 auditId,
        uint256 creditAmount,
        string ipfsHash,
        uint256 timestamp
    );

    // ---------------------------------------------------------------
    // Constructor
    // ---------------------------------------------------------------
    constructor()
        ERC1155("ipfs://terratrust/{id}.json")
        Ownable(msg.sender)
    {}

    // ---------------------------------------------------------------
    // Mint
    // ---------------------------------------------------------------

    /**
     * @notice Mint fungible carbon credits **and** a unique audit
     *         certificate NFT.
     * @param  farmer       Recipient wallet.
     * @param  auditId      Numeric audit ID (used as NFT token id).
     * @param  creditAmount Number of fungible CTT tokens to mint.
     * @param  landId       String land parcel UUID (for double-mint).
     * @param  auditYear    Calendar year of the audit.
     * @param  ipfsHash     IPFS URI of the evidence metadata.
     */
    function mintAudit(
        address farmer,
        uint256 auditId,
        uint256 creditAmount,
        string calldata landId,
        uint256 auditYear,
        string calldata ipfsHash
    ) external onlyOwner {
        // --- Double-mint prevention ------------------------------------
        bytes32 auditKey = keccak256(abi.encodePacked(landId, auditYear));
        require(!auditMinted[auditKey], "Audit already minted for this land and year");
        auditMinted[auditKey] = true;

        // --- Mint fungible credits (token id 1) ------------------------
        _mint(farmer, CARBON_CREDIT, creditAmount, "");

        // --- Mint NFT certificate (token id = auditId) -----------------
        _mint(farmer, auditId, 1, "");

        // --- Store IPFS evidence ---------------------------------------
        auditEvidence[auditId] = ipfsHash;

        emit AuditMinted(farmer, auditId, creditAmount, ipfsHash, block.timestamp);
    }

    // ---------------------------------------------------------------
    // Retire (burn)
    // ---------------------------------------------------------------

    /**
     * @notice Permanently retire (burn) carbon credits.
     * @param  amount Number of CTT tokens to retire.
     */
    function retireCredits(uint256 amount) external {
        require(
            balanceOf(msg.sender, CARBON_CREDIT) >= amount,
            "Insufficient credit balance"
        );

        _burn(msg.sender, CARBON_CREDIT, amount);
        retiredCredits[msg.sender] += amount;

        emit CreditRetired(msg.sender, amount, block.timestamp);
    }

    // ---------------------------------------------------------------
    // View helpers
    // ---------------------------------------------------------------

    /**
     * @notice Return the IPFS evidence URI for an audit certificate.
     */
    function getAuditEvidence(uint256 auditId) external view returns (string memory) {
        return auditEvidence[auditId];
    }
}
