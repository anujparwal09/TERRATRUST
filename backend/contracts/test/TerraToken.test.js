const { expect } = require("chai");
const { anyValue } = require("@nomicfoundation/hardhat-chai-matchers/withArgs");
const { ethers } = require("hardhat");

describe("TerraTrustToken", function () {
  async function waitForDeployment(contract) {
    if (typeof contract.waitForDeployment === "function") {
      await contract.waitForDeployment();
      return;
    }

    if (typeof contract.deployed === "function") {
      await contract.deployed();
    }
  }

  async function deployFixture() {
    const [owner, farmer, buyer] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("TerraTrustToken");
    const token = await Token.deploy();
    await waitForDeployment(token);
    return { token, owner, farmer, buyer };
  }

  it("mints credits and an audit certificate with the documented parameter order", async function () {
    const { token, farmer } = await deployFixture();
    const auditId = 1001n;

    await expect(
      token["mintAudit(address,uint256,uint256,string,string,uint256)"](
        farmer.address,
        auditId,
        12,
        "ipfs://cid-123",
        "land-1",
        2026
      )
    )
      .to.emit(token, "AuditMinted")
      .withArgs(farmer.address, auditId, 12, "ipfs://cid-123", anyValue);

    expect(await token.balanceOf(farmer.address, 1)).to.equal(12n);
    expect(await token.balanceOf(farmer.address, auditId)).to.equal(1n);
    expect(await token.getAuditEvidence(auditId)).to.equal("ipfs://cid-123");
  });

  it("supports the legacy mint parameter order for backward compatibility", async function () {
    const { token, farmer } = await deployFixture();
    const auditId = 1002n;

    await token["mintAudit(address,uint256,uint256,string,uint256,string)"](
      farmer.address,
      auditId,
      5,
      "land-legacy",
      2026,
      "ipfs://legacy-cid"
    );

    expect(await token.balanceOf(farmer.address, 1)).to.equal(5n);
    expect(await token.getAuditEvidence(auditId)).to.equal("ipfs://legacy-cid");
  });

  it("prevents double minting for the same land and audit year", async function () {
    const { token, farmer } = await deployFixture();

    await token["mintAudit(address,uint256,uint256,string,string,uint256)"](
      farmer.address,
      1001,
      4,
      "ipfs://cid-1",
      "land-1",
      2026
    );

    await expect(
      token["mintAudit(address,uint256,uint256,string,string,uint256)"](
        farmer.address,
        1002,
        7,
        "ipfs://cid-2",
        "land-1",
        2026
      )
    ).to.be.revertedWith("Credits already minted for this land this year");
  });

  it("retires credits and records the reason", async function () {
    const { token, farmer } = await deployFixture();

    await token["mintAudit(address,uint256,uint256,string,string,uint256)"](
      farmer.address,
      1001,
      10,
      "ipfs://cid-1",
      "land-1",
      2026
    );

    await expect(
      token.connect(farmer)["retireCredits(uint256,string)"](3, "Offset 2026 emissions")
    )
      .to.emit(token, "CreditRetired")
      .withArgs(farmer.address, 3, "Offset 2026 emissions", anyValue);

    expect(await token.balanceOf(farmer.address, 1)).to.equal(7n);
    expect(await token.retiredCredits(farmer.address)).to.equal(3n);
  });

  it("rejects minting by a non-owner account", async function () {
    const { token, farmer } = await deployFixture();

    await expect(
      token.connect(farmer)["mintAudit(address,uint256,uint256,string,string,uint256)"](
        farmer.address,
        1001,
        1,
        "ipfs://cid-1",
        "land-1",
        2026
      )
    )
      .to.be.revertedWithCustomError(token, "OwnableUnauthorizedAccount")
      .withArgs(farmer.address);
  });
});