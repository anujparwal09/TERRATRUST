const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

async function waitForDeployment(contract) {
  if (typeof contract.waitForDeployment === "function") {
    await contract.waitForDeployment();
    return;
  }

  if (typeof contract.deployed === "function") {
    await contract.deployed();
  }
}

async function getContractAddress(contract) {
  if (typeof contract.getAddress === "function") {
    return contract.getAddress();
  }

  return contract.address;
}

async function main() {
  const [deployer] = await hre.ethers.getSigners();

  console.log("Deploying TerraTrustToken with account:", deployer.address);

  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log("Account balance:", balance.toString());

  const Token = await hre.ethers.getContractFactory("TerraTrustToken");
  const token = await Token.deploy();
  await waitForDeployment(token);

  const contractAddress = await getContractAddress(token);
  console.log("TerraTrustToken deployed to:", contractAddress);

  const artifact = await hre.artifacts.readArtifact("TerraTrustToken");
  const outputPaths = [
    path.join(__dirname, "..", "artifacts", "TerraToken_ABI.json"),
    path.join(__dirname, "..", "artifacts", "TerraTrustToken_ABI.json")
  ];

  outputPaths.forEach((outputPath) => {
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, JSON.stringify(artifact.abi, null, 2));
    console.log("ABI exported to:", outputPath);
  });
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});