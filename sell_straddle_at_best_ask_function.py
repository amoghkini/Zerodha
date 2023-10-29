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
    query_expr = "name == @symbol & segment == @opt_segm & exchange == @exch"
    nearest_expiry = (
        pd.Series(pd.to_datetime(ins_mstr.query(query_expr)["expiry"].unique()))
        .sort_values()
        .dt.strftime("%Y-%m-%d")
        .tolist()[0]
    )
    query_expr = (
        "tradingsymbol == @und_symbol & segment == @und_segm & "
        + "exchange == @und_exch"
    )
    params = {
        "i": [f"{und_exch}:{ts}" for ts in ins_mstr.query(query_expr)["tradingsymbol"]]
    }
    ltp = rqs.get(url=f"{api_url}/quote", params=params)
    ltp = ltp.json()["data"][params["i"][0]]["last_price"]
    query_expr = (
        "name == @symbol & segment == @opt_segm & "
        + "exchange == @exch & expiry == @nearest_expiry"
    )
    atm_strike_as_per_spot = min(
        ins_mstr.query(query_expr)["strike"], key=lambda x: abs(x - ltp)
    )
    query_expr = (
        "name == @symbol & segment == @opt_segm & exchange =="
        + "@exch & strike == @atm_strike_as_per_spot & expiry =="
        + "@nearest_expiry"
    )
    params = {
        "i": [f"{exch}:{ts}" for ts in ins_mstr.query(query_expr)["tradingsymbol"]]
    }
    atm_strikes_quote = rqs.get(f"{api_url}/quote", params=params).json()
    atm_ce_sym, atm_pe_sym = (
        params["i"] if params["i"][0].endswith("CE") else params["i"][::-1]
    )
    atm_ce_quote, atm_pe_quote = (
        atm_strikes_quote["data"][atm_ce_sym],
        atm_strikes_quote["data"][atm_pe_sym],
    )
    s_fut_ltp = (
        atm_strike_as_per_spot + atm_ce_quote["last_price"] - atm_pe_quote["last_price"]
    )
    query_expr = (
        "name == @symbol & segment == @opt_segm & exchange == @exch & "
        + "expiry == @nearest_expiry"
    )
    atm_strike_as_per_s_fut = min(
        ins_mstr.query(query_expr)["strike"], key=lambda x: abs(x - s_fut_ltp)
    )
    query_expr = (
        "name == @symbol & segment == @opt_segm & exchange == @exch & "
        + "strike == @atm_strike_as_per_s_fut & expiry == @nearest_expiry"
    )
    atm_strikes_ts_as_per_s_fut = ins_mstr.query(query_expr)
    atm_strikes_trading_symbols = atm_strikes_ts_as_per_s_fut["tradingsymbol"]
    atm_strikes_lot_size = atm_strikes_ts_as_per_s_fut["lot_size"].values[0]
    params = {"i": [f"{exch}:{ts}" for ts in atm_strikes_trading_symbols]}
    atm_strikes_quote = rqs.get(f"{api_url}/quote", params=params).json()
    atm_ce_sym, atm_pe_sym = (
        params["i"] if params["i"][0].endswith("CE") else params["i"][::-1]
    )
    atm_ce_quote, atm_pe_quote = (
        atm_strikes_quote["data"][atm_ce_sym],
        atm_strikes_quote["data"][atm_pe_sym],
    )
    atm_ce_best_ask, atm_pe_best_ask = (
        atm_ce_quote["depth"]["sell"][0]["price"],
        atm_pe_quote["depth"]["sell"][0]["price"],
    )
    order_params = [
        {
            "exchange": "NFO",
            "tradingsymbol": tradingsymbol,
            "transaction_type": "SELL",
            "quantity": atm_strikes_lot_size * lots,
            "product": "NRML",
            "order_type": "LIMIT",
            "price": atm_ce_best_ask
            if tradingsymbol.endswith("CE")
            else atm_pe_best_ask,
        }
        for tradingsymbol in atm_strikes_trading_symbols
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
    except (KeyError, TypeError):
        print(
            f"ce_order_raw_response: {ce_order_resp}, pe_orde_raw_response: {pe_order_resp}"
        )
        return (ce_order_resp, pe_order_resp)
