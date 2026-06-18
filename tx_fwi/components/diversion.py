# components/diversion.py

from transforms.temporal import expand_monthly_to_daily

def build_diversion(monthly_df):

    df = expand_monthly_to_daily(monthly_df)

    df["component"] = "diversion"
    df["flow_role"] = "direct"
    df["count_in_basin_sum"] = 1

    return df
