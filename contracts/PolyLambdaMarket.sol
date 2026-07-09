// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title PolyLambdaMarket — a minimal on-chain binary market where the PolyLambda engine is the MM.
/// @notice TESTNET DEMO (Polygon Amoy). The engine (owner) posts a two-sided quote for a YES outcome
///         computed off-chain by the real estimators; users buy/sell YES settled in test-USDC; the
///         engine flags a dispute and resolves — the λ-dispute-defense, on-chain.
/// @dev Units: prices are 6-decimal fractions of 1 USDC (0..1e6, e.g. 0.62 -> 620000). YES shares are
///      6-decimal; 1 share redeems 1 USDC (1e6 units) if YES wins. USDC has 6 decimals on Amoy.
interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

contract PolyLambdaMarket {
    uint256 internal constant ONE = 1e6; // price scale + USDC unit (6 decimals)

    IERC20 public immutable usdc;
    address public immutable engine; // MM + admin (the backend engine wallet)

    // --- live quote (posted by the engine from estimate_lambda + compute_quote) ---
    string public category;
    uint32 public lambdaBps; // dispute intensity λ, basis points (display only)
    uint32 public sigmaBps;  // belief-vol σ, basis points (display only)
    uint256 public yesBid;   // engine buys YES at this price (0..1e6)
    uint256 public yesAsk;   // engine sells YES at this price (0..1e6)
    uint256 public maxTrade; // per-trade size cap (share units)
    uint64 public quoteTs;   // last quote timestamp

    // --- lifecycle ---
    bool public disputed;
    bool public resolved;
    bool public yesWon;

    // --- positions ---
    mapping(address => uint256) public yesShares;
    uint256 public totalYes;

    event QuotePosted(uint256 bid, uint256 ask, uint256 maxTrade, string category, uint32 lambdaBps, uint32 sigmaBps, uint64 ts);
    event Traded(address indexed user, bool buy, uint256 size, uint256 usdc, uint256 newShares);
    event Disputed(uint64 ts);
    event Resolved(bool yesWon);
    event Redeemed(address indexed user, uint256 payout);
    event Collateral(uint256 amount, uint256 balance);

    modifier onlyEngine() {
        require(msg.sender == engine, "not engine");
        _;
    }

    constructor(address _usdc) {
        usdc = IERC20(_usdc);
        engine = msg.sender;
    }

    /// @notice Engine deposits USDC collateral to back YES payouts.
    function fund(uint256 amount) external onlyEngine {
        require(usdc.transferFrom(msg.sender, address(this), amount), "fund xfer");
        emit Collateral(amount, usdc.balanceOf(address(this)));
    }

    /// @notice Engine posts a fresh two-sided quote (from the real estimators, off-chain).
    function postQuote(uint256 bid, uint256 ask, uint256 _maxTrade, string calldata cat, uint32 lam, uint32 sig)
        external
        onlyEngine
    {
        require(!resolved, "resolved");
        require(bid < ask && ask <= ONE, "bad px");
        yesBid = bid;
        yesAsk = ask;
        maxTrade = _maxTrade;
        category = cat;
        lambdaBps = lam;
        sigmaBps = sig;
        quoteTs = uint64(block.timestamp);
        emit QuotePosted(bid, ask, _maxTrade, cat, lam, sig, quoteTs);
    }

    /// @notice User buys `size` YES shares at the engine's ask; pays size*ask/1e6 USDC into escrow.
    function buyYes(uint256 size) external {
        require(!disputed && !resolved, "closed");
        require(size > 0 && size <= maxTrade, "size");
        uint256 cost = (size * yesAsk) / ONE;
        require(usdc.transferFrom(msg.sender, address(this), cost), "buy xfer");
        yesShares[msg.sender] += size;
        totalYes += size;
        emit Traded(msg.sender, true, size, cost, yesShares[msg.sender]);
    }

    /// @notice User sells `size` YES shares back to the engine at the bid; receives size*bid/1e6 USDC.
    function sellYes(uint256 size) external {
        require(!resolved, "resolved");
        require(size > 0 && size <= yesShares[msg.sender], "size");
        uint256 proceeds = (size * yesBid) / ONE;
        yesShares[msg.sender] -= size;
        totalYes -= size;
        require(usdc.transfer(msg.sender, proceeds), "sell xfer");
        emit Traded(msg.sender, false, size, proceeds, yesShares[msg.sender]);
    }

    /// @notice Engine flags a dispute — halts new buys (the λ-defense: pull the dangerous side).
    function flagDispute() external onlyEngine {
        disputed = true;
        emit Disputed(uint64(block.timestamp));
    }

    /// @notice Engine resolves the market. YES holders can then redeem 1 USDC/share iff yesWon.
    function resolve(bool _yesWon) external onlyEngine {
        resolved = true;
        yesWon = _yesWon;
        emit Resolved(_yesWon);
    }

    /// @notice User redeems their YES shares after resolution (1 share = 1 USDC on a YES win).
    function redeem() external {
        require(resolved, "unresolved");
        uint256 s = yesShares[msg.sender];
        require(s > 0, "no shares");
        yesShares[msg.sender] = 0;
        uint256 payout = yesWon ? s : 0; // 1 share (6-dec) == 1 USDC (6-dec)
        if (payout > 0) require(usdc.transfer(msg.sender, payout), "redeem xfer");
        emit Redeemed(msg.sender, payout);
    }

    /// @notice Engine sweeps leftover collateral (after resolution).
    function withdraw(uint256 amount) external onlyEngine {
        require(usdc.transfer(engine, amount), "wd xfer");
    }

    /// @notice Convenience view for the backend/frontend.
    function snapshot()
        external
        view
        returns (
            uint256 _bid,
            uint256 _ask,
            uint256 _maxTrade,
            uint64 _quoteTs,
            bool _disputed,
            bool _resolved,
            bool _yesWon,
            uint256 _totalYes,
            uint256 _escrow,
            string memory _category,
            uint32 _lambdaBps,
            uint32 _sigmaBps
        )
    {
        return (
            yesBid,
            yesAsk,
            maxTrade,
            quoteTs,
            disputed,
            resolved,
            yesWon,
            totalYes,
            usdc.balanceOf(address(this)),
            category,
            lambdaBps,
            sigmaBps
        );
    }
}
