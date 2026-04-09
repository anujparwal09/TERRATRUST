// SPDX-License-Identifier: MIT
pragma solidity 0.8.29;

import "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title  TerraTrustToken
 * @notice ERC-1155 contract for TerraTrust-AR carbon credits.
 *
 *         Token ID 1       = Fungible carbon credits (CTT).
 *         Token ID >= 1000 = Non-fungible audit certificates.
 *
 *         Double-mint prevention uses keccak256(landId, auditYear).
 */
contract TerraTrustToken is ERC1155, Ownable {
    uint256 public constant CARBON_CREDIT = 1;

    /// @notice IPFS evidence URI for each audit certificate tokenId.
    mapping(uint256 => string) public auditEvidence;

    /// @notice Tracks whether a (landId, auditYear) combo has been minted.
    mapping(bytes32 => bool) public auditMinted;

    /// @notice Retired (burned) credit balance per address.
    mapping(address => uint256) public retiredCredits;

    event CreditRetired(
        address indexed retiredBy,
        uint256 amount,
        string retirementReason,
        uint256 timestamp
    );

    event AuditMinted(
        address indexed farmer,
        uint256 auditId,
        uint256 creditAmount,
        string ipfsHash,
        uint256 timestamp
    );

    constructor() ERC1155("") Ownable(msg.sender) {}

    /**
     * @notice Documented parameter order from the v3.1 backend spec.
     */
    function mintAudit(
        address farmer,
        uint256 auditId,
        uint256 creditAmount,
        string calldata ipfsHash,
        string calldata landId,
        uint256 auditYear
    ) external onlyOwner {
        _mintAuditRecord(farmer, auditId, creditAmount, ipfsHash, landId, auditYear);
    }

    /**
     * @notice Legacy parameter order retained for backward compatibility.
     */
    function mintAudit(
        address farmer,
        uint256 auditId,
        uint256 creditAmount,
        string calldata landId,
        uint256 auditYear,
        string calldata ipfsHash
    ) external onlyOwner {
        _mintAuditRecord(farmer, auditId, creditAmount, ipfsHash, landId, auditYear);
    }

    function _mintAuditRecord(
        address farmer,
        uint256 auditId,
        uint256 creditAmount,
        string calldata ipfsHash,
        string calldata landId,
        uint256 auditYear
    ) internal {
        bytes32 auditKey = keccak256(abi.encodePacked(landId, auditYear));
        require(!auditMinted[auditKey], "Credits already minted for this land this year");
        auditMinted[auditKey] = true;

        _mint(farmer, CARBON_CREDIT, creditAmount, "");
        _mint(farmer, auditId, 1, "");

        auditEvidence[auditId] = ipfsHash;

        emit AuditMinted(farmer, auditId, creditAmount, ipfsHash, block.timestamp);
    }

    /**
     * @notice Permanently retire (burn) carbon credits.
     */
    function retireCredits(uint256 amount, string memory reason) public {
        require(
            balanceOf(msg.sender, CARBON_CREDIT) >= amount,
            "Insufficient credits to retire"
        );

        _burn(msg.sender, CARBON_CREDIT, amount);
        retiredCredits[msg.sender] += amount;

        emit CreditRetired(msg.sender, amount, reason, block.timestamp);
    }

    function retireCredits(uint256 amount) external {
        retireCredits(amount, "");
    }

    /**
     * @notice Return the IPFS evidence URI for an audit certificate.
     */
    function getAuditEvidence(uint256 auditId) external view returns (string memory) {
        return auditEvidence[auditId];
    }
}

contract TerraToken is TerraTrustToken {}
