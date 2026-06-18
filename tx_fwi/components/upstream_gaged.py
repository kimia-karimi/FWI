# components/gaged_upstream.py

def build_upstream(ctx, start, end):

    rows = []

    for estuary, comps in ctx.upstream.items():
        for comp in comps:

            s = fetch_upstream(
                comp["source"],
                comp["gage_id"],
                start, end,
                comp.get("special")
            )

            if s.empty:
                continue

            df = s.reset_index()
            df.columns = ["date","value_afday"]

            df["id"] = comp["component_id"]
            df["id_type"] = "system"
            df["estuary"] = estuary
            df["component"] = "gaged_upstream"
            df["source"] = comp["source"]
            df["flow_role"] = "system_counted"
            df["count_in_basin_sum"] = 1
            df["note"] = comp.get("label")

            rows.append(df)

    return pd.concat(rows)
