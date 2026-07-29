from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext

from tx_fwi.sources.models import (
    load_txrr_flow,
)


REQUIRED_OUT_COLS = [
    "date",
    "id",
    "id_type",
    "estuary",
    "component",
    "source",
    "value_afday",
    "flow_role",
    "count_in_basin_sum",
    "note",
]


class UngagedComponent:
    """
    Build daily ungaged watershed flows
    from TxRR model outputs.

    Produces canonical FWI schema rows.
    """

    name = "ungaged"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx

    # --------------------------------------------------
    def build(
        self,
        start,
        end,
    ) -> pd.DataFrame:

        watersheds = (
            self.ctx.registry
            .load_watersheds()
        )

        if "HAS_UNGAGE" not in watersheds.columns:
            raise ValueError(
                "Registry missing HAS_UNGAGE field."
            )

        reg = watersheds[
            watersheds["HAS_UNGAGE"] == 1
        ].copy()

        if reg.empty:
            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        model_dir = self.ctx.config["txrr_dir"]

        rows = []

        for _, r in reg.iterrows():

            ws_id = (
                str(r["WS_ID"])
                .strip()
                .zfill(5)
            )

            estuary = r.get(
                "ESTUARY",
                None,
            )

            try:

                s = load_txrr_flow(
                    ws_id=ws_id,
                    start=start,
                    end=end,
                    model_dir=model_dir,
                )

            except FileNotFoundError:

                print(
                    f"[Ungaged] "
                    f"Missing model file: "
                    f"{ws_id}"
                )

                continue

            except Exception as e:

                print(
                    f"[Ungaged] "
                    f"Failed {ws_id}: {e}"
                )

                continue

            if s is None or s.empty:
                continue

            df = s.reset_index()

            df.columns = [
                "date",
                "value_afday",
            ]

            df["id"] = ws_id

            df["id_type"] = "watershed"

            df["estuary"] = estuary

            df["component"] = self.name

            df["source"] = "txrr"

            #
            # Direct watershed contribution
            #
            df["flow_role"] = "direct"

            df["count_in_basin_sum"] = 1

            df["note"] = None

            rows.append(df)

        if not rows:

            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        out = pd.concat(
            rows,
            ignore_index=True,
        )

        out["date"] = pd.to_datetime(
            out["date"]
        )

        return out[
            REQUIRED_OUT_COLS
        ]

    # --------------------------------------------------
    def run(
        self,
        start=None,
        end=None,
    ):

        #
        # Incremental processing
        #
        if start is None:

            wm = (
                self.ctx.storage
                .get_watermark(
                    self.name,
                    default="2015-01-01",
                )
            )

            if wm is not None:

                start = (
                    pd.Timestamp(wm)
                    + pd.Timedelta(days=1)
                )

            else:

                start = pd.Timestamp(
                    "2015-01-01"
                )

        if end is None:

            end = (
                pd.Timestamp.utcnow()
                .normalize()
            )

        print(
            f"[Ungaged] "
            f"Running "
            f"{start.date()} "
            f"→ "
            f"{end.date()}"
        )

        df = self.build(
            start=start,
            end=end,
        )

        if df.empty:

            print(
                "[Ungaged] "
                "No rows written."
            )

            return 0

        n = self.ctx.storage.append(df)

        #
        # Update watermark
        #
        self.ctx.storage.set_watermark(
            self.name,
            df["date"].max(),
        )

        print(
            f"[Ungaged] "
            f"Wrote "
            f"{n:,} rows."
        )

        return n
