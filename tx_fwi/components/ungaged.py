def build_ungaged(txrr_df):
    
    df = txrr_df.copy()
    df["component"] = "ungaged"
    df["flow_role"] = "direct"
    df["count_in_basin_sum"] = 1

    return df
