const hre = require("hardhat");

async function main() {

  const [deployer] = await hre.ethers.getSigners();

  console.log("Deploying TerraToken with account:", deployer.address);

  const balance = await deployer.getBalance();
  console.log("Account balance:", balance.toString());

  const Token = await hre.ethers.getContractFactory("TerraToken");

  const token = await Token.deploy();

  // correct function for ethers v5
  await token.deployed();

  console.log("TerraToken deployed to:", token.address);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});