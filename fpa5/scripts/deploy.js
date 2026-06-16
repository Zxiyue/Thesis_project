const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await ethers.getSigners();
  const AuditBoard = await ethers.getContractFactory("AuditBoard");
  const board = await AuditBoard.deploy(deployer.address);
  await board.waitForDeployment();
  const address = await board.getAddress();
  console.log("AuditBoard deployed to:", address);
  const artifact = await hre.artifacts.readArtifact("AuditBoard");
  const outDir = path.join(__dirname, "..", "outputs");
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(
    path.join(outDir, "contract.json"),
    JSON.stringify({ address, abi: artifact.abi, kgc: deployer.address }, null, 2)
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
