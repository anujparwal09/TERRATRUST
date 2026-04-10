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

    await token.mintAudit(
      farmer.address,
      auditId,
      12,
      "ipfs://cid-123",
      "land-1",
      2026
    );

    expect(await token.balanceOf(farmer.address, 1)).to.equal(12n);
    expect(await token.balanceOf(farmer.address, auditId)).to.equal(1n);
    expect(await token.getAuditEvidence(auditId)).to.equal("ipfs://cid-123");
  });

  it("prevents double minting for the same land and audit year", async function () {
    const { token, farmer } = await deployFixture();

    await token.mintAudit(
      farmer.address,
      1001,
      4,
      "ipfs://cid-1",
      "land-1",
      2026
    );

    await expect(
      token.mintAudit(
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

    await token.mintAudit(
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
      token.connect(farmer).mintAudit(
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