# components/gaged_local.py

import pandas as pd

def build_local_gaged(ctx, start, end):

    gdf = ctx.registry
    rows = []

    subset = gdf[gdf["HAS_GAGED"] == 1]

    for _, r in subset.iterrows():
        ws_id = r["WS_ID"]
        estuary = r["ESTUARY"]
        source = r["G_SOURCE"]
        gage_id = r["GAGE_ID"]
        special = r["SPECIAL_T"]

        # --- fetch ---
        s = fetch_local(source, gage_id, start, end, special)

        if s.empty:
            continue

        df = s.reset_index()
        df.columns = ["date","value_afday"]

        df["id"] = ws_id
        df["id_type"] = "watershed"
        df["estuary"] = estuary
        df["component"] = "gaged_local"
        df["source"] = source
        df["flow_role"] = "adjusted" if special else "direct"
        df["count_in_basin_sum"] = 1
        df["note"] = special

        rows.append(df)

    return pd.concat(rows)
