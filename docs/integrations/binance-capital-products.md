# Binance capital-product map

Document type: maintained integration evidence.

Verified against the Binance Developer API catalog on 2026-08-19. This map is
an input to capability design, not a promise that every endpoint is already
implemented.

Primary source: [Binance API catalog](https://developers.binance.com/en/docs/catalog)
and its [machine-readable documentation index](https://developers.binance.com/en/docs/llms.txt).

## Capital locations and movement

Binance exposes several independently observable and addressable locations:

- Spot, Funding Wallet, Cross Margin, Isolated Margin;
- USD-M Futures, COIN-M Futures, Options;
- Portfolio Margin and Portfolio Margin Pro;
- master, subaccount, managed subaccount, and broker subaccount scopes;
- yield-product positions that are not immediately available trading balance.

Relevant operation families include:

- User Universal Transfer plus transfer-history query;
- master/subaccount Universal Transfer plus history;
- dedicated subaccount futures, margin, sub-to-master, and sub-to-sub transfer;
- Funding Wallet and aggregate wallet-balance queries.

Current implementation status: the concrete `BinanceCapitalRestConnection`
implements same-Account User Universal Transfer submit/history for explicitly
bound Spot, Funding, USD-M, COIN-M, and Cross Margin segments. Binance does not
accept a client idempotency key on this endpoint, so Capital persists a
delivery fence and reconciles by `tranId` or an unambiguous route/asset/amount/
time match. Master/subaccount and other cross-Account rails remain separate
pending implementations.

These map to `AssetTransferCommand` and `AssetTransferStatusQuery` only when
both endpoints are locations within Binance. A blockchain withdrawal or a
cross-exchange movement is not an atomic asset transfer; it needs a separate
withdrawal/deposit workflow with network, address, fee, confirmation, and
compliance state.

## Simple Earn and yield-bearing assets

The Simple Earn API currently exposes more than a Flexible/Locked enum:

| Family | Queries | Commands |
|---|---|---|
| Flexible | product list, position, personal quota, subscription preview, rates, rewards, subscription/redemption/collateral history | subscribe, redeem, set auto-subscribe |
| Locked | product list, position, personal quota, subscription preview, rewards and subscription/redemption history | subscribe, redeem, set auto-subscribe, set redeem option |
| BFUSD | account, quota, rates, rewards, subscription/redemption history | subscribe, redeem |
| RWUSD | account, quota, rates, rewards, subscription/redemption history | subscribe, redeem |

Consequences for the neutral model:

- product-wide maximum and principal-specific remaining quota are different;
- subscription acknowledgement, product position, rewards, and redemption
  settlement are different facts;
- auto-subscribe and redeem options are configuration commands, not fields on
  a one-time subscription;
- BFUSD and RWUSD are represented as yield-bearing-asset products rather than
  pretending they are ordinary Flexible products.

## Staking

The Staking API currently contains several distinct workflows:

- ETH staking, ETH redemption, WBETH rewards and BETH/WBETH wrapping;
- SOL staking, SOL redemption, BNSOL rewards, unclaimed and boost rewards;
- On-chain Yields Locked products with quota, positions, rewards, subscription,
  redemption, and redeem-option configuration;
- Soft Staking product discovery, enablement, and reward history.

Basic staking products may implement the common Earn query/subscribe/redeem
surface when their lifecycle fits it. Token wrapping, reward claiming, and
provider-specific configuration remain explicit Binance operations until a
second production participant proves a stable shared capability.

## Advanced Earn is not ordinary Earn

Binance exposes two structured-investment families:

- Dual Investment: product list, account, positions, subscription, and
  auto-compound configuration;
- Discount Buy: product list, aggregate holdings, positions, and subscription.

These products have conditional settlement and market exposure. They must not
implement the plain `EarnCommand` merely because Binance places them under
Advanced Earn. A future structured-investment capability needs explicit
underlying asset, settlement asset, strike or target terms, maturity, payoff,
and worst-case exposure so Risk can authorize it.

## Borrowing is a liability axis

Margin borrowing/repayment, Crypto Loan, VIP Loan, and Institutional Loan
change liabilities and collateral. They are not negative Earn positions and
must remain outside the Earn capability. Their eventual business model needs
loan principal, collateral, interest, liquidation threshold, maturity, and
repayment lifecycle.

## Recommended first implementation

The first Binance end-to-end slice should stay narrow:

1. observe Funding Wallet, USD-M, and Simple Earn Flexible balances/positions;
2. query a Flexible product and the principal's remaining quota;
3. subscribe with an idempotent Capital operation;
4. query subscription/position state and reconcile Account facts;
5. redeem, reconcile the resulting Funding Wallet balance;
6. Universal Transfer to USD-M, query transfer history, and reconcile
   available margin before Execution can use it.

This path validates the full Capital control loop without prematurely treating
structured investments, loans, staking wrappers, or blockchain withdrawals as
the same operation.
