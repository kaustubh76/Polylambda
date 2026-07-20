"""Shared in-memory FakeChain/FakeSigner twins of PolyLambdaMarket semantics (test helper)."""

U = 10**6


class _Call:
    def __init__(self, chain, addr, name, args):
        self.chain, self.addr, self.name, self.args = chain, addr, name, args


class _Fns:
    def __init__(self, chain, addr):
        self._chain, self._addr = chain, addr

    def postQuote(self, *args):
        return _Call(self._chain, self._addr, "postQuote", args)

    def flagDispute(self, *args):
        return _Call(self._chain, self._addr, "flagDispute", args)


class _Contract:
    def __init__(self, chain, addr):
        self.functions = _Fns(chain, addr)


class FakeChain:
    """Reader + market-state twin. Signer semantics live in FakeSigner.send."""

    def __init__(self, *, bid=0.55, ask=0.65, max_trade=0.5, escrow=1.0, total_yes=0.0,
                 quote_ts=1000, head=100):
        self.state = {"bid": bid, "ask": ask, "max_trade": max_trade, "quote_ts": quote_ts,
                      "disputed": False, "resolved": False, "yes_won": False,
                      "total_yes": total_yes, "escrow_usdc": escrow, "category": "politics",
                      "lambda_jump": 0.0, "sigma": 0.0}
        self.head = head
        self.events: list[dict] = []
        self.snapshot_calls = 0

    # reader protocol
    def head_block(self):
        return self.head

    def snapshot(self, address):
        self.snapshot_calls += 1
        return {"deployed": True, **self.state}

    def traded_logs(self, address, from_block, to_block):
        return [e for e in self.events if from_block <= e["block"] <= to_block]

    def contract(self, address):
        return _Contract(self, address)

    # user-side actions (append Traded events, mutate state — contract semantics)
    def user_buy(self, size):
        assert not self.state["disputed"] and not self.state["resolved"], "closed"
        usdc = size * self.state["ask"]
        self.head += 1
        self.state["escrow_usdc"] += usdc
        self.state["total_yes"] += size
        self.events.append({"user": "0xuser", "buy": True, "size": size, "usdc": usdc,
                            "new_shares": size, "block": self.head, "log_index": 0,
                            "tx": f"0xbuy{len(self.events)}", "timestamp": 1_700_000_000 + self.head})

    def user_sell(self, size):
        usdc = size * self.state["bid"]
        self.head += 1
        self.state["escrow_usdc"] -= usdc
        self.state["total_yes"] -= size
        self.events.append({"user": "0xuser", "buy": False, "size": size, "usdc": usdc,
                            "new_shares": 0.0, "block": self.head, "log_index": 0,
                            "tx": f"0xsell{len(self.events)}", "timestamp": 1_700_000_000 + self.head})


class FakeSigner:
    def __init__(self, chain):
        self.chain = chain
        self.sent: list[_Call] = []
        self.fail_next = 0

    def send(self, call: _Call):
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("rpc boom")
        st = self.chain.state
        if call.name == "postQuote":
            bid6, ask6, mt6, cat, lam, sig = call.args
            assert 0 <= bid6 < ask6 <= U, "contract would revert: require(bid < ask <= ONE)"
            assert not st["resolved"]
            st.update(bid=bid6 / U, ask=ask6 / U, max_trade=mt6 / U, category=cat,
                      lambda_jump=lam / 10000, sigma=sig / 10000)
        elif call.name == "flagDispute":
            st["disputed"] = True
        self.chain.head += 1
        self.sent.append(call)
        return {"tx": f"0xtx{len(self.sent)}", "gas_pol": 0.002, "block": self.chain.head}


