def punch_short_straddle_at_best_ask(
    symbol: str, enctoken: str, exch: str = "NFO", lots: int = 1
):
    (pd, rqs, api_url, idx_sym) = (
        __import__("pandas"),
        __import__("requests").session(),
        "https://api.kite.trade",
        {
            "NIFTY": "NIFTY 50",
            "BANKNIFTY": "NIFTY BANK",
            "FINNIFTY": "NIFTY FIN SERVICE",
            "MIDCPNIFTY": "NIFTY MID SELECT",
        },
    )
    (und_symbol, und_exch, und_segm, opt_segm) = (
        idx_sym[symbol] if symbol in idx_sym else symbol,
        "BSE" if exch == "BFO" else "NSE" if exch == "NFO" else "CDS",
        "INDICES",
        f"{exch}-OPT",
    )
    rqs.headers.update({"Authorization": f"enctoken {enctoken}"})
    ins_mstr = pd.read_csv(f"{api_url}/instruments")
    nearest_expiry = (
        pd.Series(
            pd.to_datetime(
                ins_mstr.query(
                    "name == @symbol & segment == @opt_segm & exchange == @exch"
                )["expiry"].unique()
            )
        )
        .sort_values()
        .dt.strftime("%Y-%m-%d")
        .tolist()[0]
    )
    params = {
        "i": list(
            map(
                lambda x: f"{und_exch}:{x}",
                ins_mstr.query(
                    "tradingsymbol == @und_symbol & segment == @und_segm & exchange == @und_exch"
                )["tradingsymbol"],
            )
        )
    }
    ltp = rqs.get(f"{api_url}/quote", params=params).json()["data"][params["i"][0]][
        "last_price"
    ]
    atm_strike_as_per_spot = min(
        ins_mstr.query(
            "name == @symbol & segment == @opt_segm & exchange == @exch & expiry == @nearest_expiry"
        )["strike"],
        key=lambda x: abs(x - ltp),
    )
    params = {
        "i": list(
            map(
                lambda x: f"{exch}:{x}",
                ins_mstr.query(
                    "name == @symbol & segment == @opt_segm & exchange == @exch & strike == @atm_strike_as_per_spot & expiry == @nearest_expiry"
                )["tradingsymbol"],
            )
        )
    }
    syn_fut = rqs.get(
        f"{api_url}/quote",
        params=params,
    ).json()["data"]
    syn_fut_price = (
        atm_strike_as_per_spot
        + syn_fut[[i for i in params["i"] if i.endswith("CE")][0]]["last_price"]
        - syn_fut[[i for i in params["i"] if i.endswith("PE")][0]]["last_price"]
    )
    atm_strike_as_per_syn_fut = min(
        ins_mstr.query(
            "name == @symbol & segment == @opt_segm & exchange == @exch & expiry == @nearest_expiry"
        )["strike"],
        key=lambda x: abs(x - syn_fut_price),
    )
    atm_ce_pe_as_per_syn_fut = ins_mstr.query(
        "name == @symbol & segment == @opt_segm & exchange == @exch & strike == @atm_strike_as_per_syn_fut & expiry == @nearest_expiry"
    )
    lot_size = atm_ce_pe_as_per_syn_fut["lot_size"].values[0]
    params = {
        "i": list(
            map(
                lambda x: f"{exch}:{x}",
                atm_ce_pe_as_per_syn_fut["tradingsymbol"],
            )
        )
    }
    atm_ce_pe_quote = rqs.get(f"{api_url}/quote", params=params).json()["data"]
    atm_ce_best_ask, atm_pe_best_ask = (
        atm_ce_pe_quote[[i for i in params["i"] if i.endswith("CE")][0]]["depth"][
            "sell"
        ][0]["price"],
        atm_ce_pe_quote[[i for i in params["i"] if i.endswith("PE")][0]]["depth"][
            "sell"
        ][0]["price"],
    )
    order_params = [
        {
            "exchange": "NFO",
            "tradingsymbol": tradingsymbol,
            "transaction_type": "SELL",
            "quantity": lot_size * lots,
            "product": "NRML",
            "order_type": "LIMIT",
            "price": atm_ce_best_ask
            if tradingsymbol.endswith("CE")
            else atm_pe_best_ask,
        }
        for tradingsymbol in atm_ce_pe_as_per_syn_fut["tradingsymbol"]
    ]
    ce_order_resp, pe_order_resp = [
        rqs.post(
            f"{api_url}/orders/regular",
            data=order_param,
        ).json()
        for order_param in order_params
    ]
    try:
        ce_order_id, pe_order_id = (
            ce_order_resp["data"]["order_id"],
            pe_order_resp["data"]["order_id"],
        )
        print(f"ce_order_id: {ce_order_id}, pe_order_id: {pe_order_id}")
        return (ce_order_id, pe_order_id)
    except KeyError:
        print(f"ce_order_raw_response: {ce_order_resp}, pe_orde_raw_response: {pe_order_resp}")
        return (ce_order_resp, pe_order_resp)
