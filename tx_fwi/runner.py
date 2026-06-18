# runner.py

def run_all():

    ctx = load_context()

    start_local = ctx.storage.get_watermark("gaged_local") or pd.Timestamp("2015-01-01")
    start_upstream = ctx.storage.get_watermark("gaged_upstream") or pd.Timestamp("2015-01-01")

    end = pd.Timestamp.utcnow().normalize()

    df_local = build_local_gaged(ctx, start_local, end)
    df_upstream = build_upstream(ctx, start_upstream, end)

    all_data = pd.concat([df_local, df_upstream])

    ctx.storage.append(all_data)

    ctx.storage.set_watermark("gaged_local", df_local["date"].max())
    ctx.storage.set_watermark("gaged_upstream", df_upstream["date"].max())
