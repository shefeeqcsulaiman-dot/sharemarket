def backtest_signals(df):
    trades = []
    position = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        if position is None:
            if row.get("signal") == "BUY":
                position = {
                    "entry_price": float(row.get("close", 0)),
                    "entry_time": row.get("timestamp")
                }
        else:
            pnl = float(row.get("close", 0)) - position["entry_price"]
            pnl_pct = pnl / position["entry_price"] * 100 if position["entry_price"] else 0
            if pnl_pct >= 2 or pnl_pct <= -1:
                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": row.get("timestamp"),
                    "entry": position["entry_price"],
                    "exit": float(row.get("close", 0)),
                    "pnl_pct": round(pnl_pct, 2)
                })
                position = None

    return trades
