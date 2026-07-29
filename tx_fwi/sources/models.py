"""Load model-produced daily watershed outputs such as TxRR or future HEC-HMS outputs."""
# tx_fwi/sources/models.py

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_txrr_flow(
    ws_id,
    start,
    end,
    model_dir="T:/CoastalScience/Data/Hydrology/fwi_master/Simulations\Simulations",
):

    path = Path(model_dir) / f"{ws_id}m"

    if not path.exists():
        raise FileNotFoundError(path)

    df = pd.read_csv(
        path,
        delim_whitespace=True,
    )

    #
    # expected:
    #
    # year month day wsdmafd
    #

    flow_col = df.columns[-1]

    df["date"] = pd.to_datetime(
        dict(
            year=df["year"],
            month=df["month"],
            day=df["day"],
        )
    )

    df = df[
        (df["date"] >= pd.Timestamp(start))
        &
        (df["date"] <= pd.Timestamp(end))
    ]

    s = pd.Series(
        df[flow_col].values,
        index=df["date"],
        name="value_afday",
    )

    return s.sort_index()
