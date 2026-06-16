// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract AuditBoard {
    address public kgc;
    bool public initialized;
    uint256 public latestRound;
    bytes32 public latestAuditRoot;
    bytes32 public latestModelHash;

    struct InitTx {
        bytes32 sysParaHash;
        bytes32 uRoot;
        bytes32 modelHash0;
        bytes32 auditRoot0;
        bytes sigInit;
    }

    struct FinalTx {
        uint256 round;
        bytes32 alpha;
        bytes32 rootUp;
        bytes32 comAggHash;
        bytes32 modelHashR;
        bytes32 modelHashNext;
        bytes32 auditRoot;
        bytes sigFinal;
    }

    event InitSubmitted(bytes32 indexed auditRoot0, bytes32 indexed modelHash0, bytes32 sysParaHash, bytes32 uRoot);
    event FinalSubmitted(uint256 indexed round, bytes32 indexed auditRoot, bytes32 indexed modelHashNext, bytes32 rootUp, bytes32 comAggHash);
    event FraudProofSubmitted(uint256 indexed round, bytes32 indexed clientIdHash, bytes32 receiptHash, bytes32 rootUp, string claimType);

    modifier onlyKGC() {
        require(msg.sender == kgc, "only KGC");
        _;
    }

    constructor(address kgcAddress) {
        kgc = kgcAddress;
    }

    function submitInit(
        bytes32 sysParaHash,
        bytes32 uRoot,
        bytes32 modelHash0,
        bytes32 auditRoot0,
        bytes calldata sigInit
    ) external onlyKGC {
        require(!initialized, "already initialized");
        bytes32 expected = sha256(abi.encodePacked("AUDIT_INIT", sysParaHash, uRoot, modelHash0));
        require(expected == auditRoot0, "bad auditRoot0");
        initialized = true;
        latestRound = 0;
        latestAuditRoot = auditRoot0;
        latestModelHash = modelHash0;
        emit InitSubmitted(auditRoot0, modelHash0, sysParaHash, uRoot);
    }

    function submitFinal(
        uint256 round,
        bytes32 alpha,
        bytes32 rootUp,
        bytes32 comAggHash,
        bytes32 modelHashR,
        bytes32 modelHashNext,
        bytes32 auditRoot,
        bytes calldata sigFinal
    ) external onlyKGC {
        require(initialized, "not initialized");
        require(round == latestRound + 1, "bad round");
        require(modelHashR == latestModelHash, "bad model chain");
        bytes32 expected = sha256(abi.encodePacked(latestAuditRoot, round, alpha, rootUp, comAggHash, modelHashR, modelHashNext));
        require(expected == auditRoot, "bad audit root");
        latestRound = round;
        latestAuditRoot = auditRoot;
        latestModelHash = modelHashNext;
        emit FinalSubmitted(round, auditRoot, modelHashNext, rootUp, comAggHash);
    }

    function submitFraudProof(
        uint256 round,
        bytes32 clientIdHash,
        bytes32 receiptHash,
        bytes32 rootUp,
        string calldata claimType,
        bytes calldata proofData,
        bytes calldata sigClient
    ) external {
        require(initialized, "not initialized");
        emit FraudProofSubmitted(round, clientIdHash, receiptHash, rootUp, claimType);
    }
}
