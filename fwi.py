import geopandas as gpd
import pandas as pd
from pathlib import Path

# ============================================================
# USER INPUTS
# ============================================================

INPUT_SHP = Path(r"\\\\T:\\CoastalScience\\Data\\Hydrology\\fwi_master\\watersheds\\TWDB_watersheds.shp")

OUTPUT_SHP = Path(r"\\\\T:\\CoastalScience\\Data\\Hydrology\\fwi_master\\watersheds\\watersheds_registry.shp")
# ---------------------------------------------------------------------
# REQUIRED: watershed -> estuary mapping
# Use strings for WS_ID to avoid issues with leading zeros / mixed values
# ---------------------------------------------------------------------
# ESTUARY_MAP = {
#     # Sabine
#     "5008": "Sabine",
#     "5010": "Sabine",
#     "5012": "Sabine",
#     "5030": "Sabine",
#     "5110": "Sabine",
#     "6010": "Sabine",
#     "6020": "Sabine",
#     "6070": "Sabine",
#     "6080": "Sabine",

#     # Laguna Madre (examples from your sample)
#     "22024": "Laguna_Madre",
#     "22025": "Laguna_Madre",
#     "22027": "Laguna_Madre",
#     "22030": "Laguna_Madre",
#     "22040": "Laguna_Madre",
#     "22041": "Laguna_Madre",
#     "22911": "Laguna_Madre",
#     "22909": "Laguna_Madre",

#     # Add the rest of your watersheds here...
# }

# ---------------------------------------------------------------------
# # OPTIONAL: watershed -> river/system name
# # ---------------------------------------------------------------------
# RIVER_MAP = {
#     "22911": "Rio_Grande",
#     "22909": "Rio_Grande",
#     "14010": "Colorado",
#     "12001": "Brazos",
#     "12002": "Brazos",
#     # Add more as needed
# }

# # ---------------------------------------------------------------------
# # OPTIONAL: terminal watersheds (1 = terminal/outlet watershed to estuary)
# # ---------------------------------------------------------------------
# TERMINAL_WS = {
#     "14010",
#     "12002",
#     "22911",
#     "22909",
#     # Add more as needed
# }

# ---------------------------------------------------------------------
# LOCAL GAGES:
# gage is downstream of that watershed and directly represents that WS
#
# schema:
#   WS_ID: {
#       "source": "usgs" | "ibwc" | "lnra" | "special",
#       "gage_id": "08162500",
#       "special": "colorado_adjusted"   # optional
#   }
# ---------------------------------------------------------------------
LOCAL_GAGES = {
    "14010": {"source": "usgs", "gage_id": "08162500", "special": "colorado_adjusted"},
   # "22911": {"source": "ibwc", "gage_id": "08470200"},
    #"22909": {"source": "ibwc", "gage_id": "08470400"},
    # Sabine:
    #"05030": {"source": "usgs", "gage_id": "08030500"}, #there is no watershed 05030
    "05110": {"source": "usgs", "gage_id": "08031000"},
    "06020": {"source": "usgs", "gage_id": "08041000"},
    "06070": {"source": "usgs", "gage_id": "08041700"},
    "06080": {"source": "usgs", "gage_id": "08041500"},
    #Galveston
    "08110": {"source": "usgs", "gage_id": "08067000"},
    "09030": {"source": "usgs", "gage_id": "08067500"},
    "10061": {"source": "usgs", "gage_id": "08075000"},
    #"10062": {"source": "usgs", "gage_id": "08075500"}, This station has no data after 09/30/1995. considered as an ungaged watersehd.
    "10063": {"source": "usgs", "gage_id": "08076000"},
    "10064": {"source": "usgs", "gage_id": "08076500"},
    "10065": {"source": "usgs", "gage_id": "08075770"},
    "10066": {"source": "usgs", "gage_id": "08075730"},
    "10073": {"source": "usgs", "gage_id": "08074500"},
    "10074": {"source": "usgs", "gage_id": "08073600"},
    #"11021": {"source": "usgs", "gage_id": "08076997"},  no data after 1994-09-04 (this is now an ungauged watershed)
    "11081": {"source": "usgs", "gage_id": "08078000"},
    "10020": {"source": "usgs", "gage_id": "08072000", "special": "lake_houston"},
    #Matagorda
    "15020": {"source": "usgs", "gage_id": "08162600"},
    "16005": {"source": "usgs", "gage_id": "08164000"},
    "17020": {"source": "usgs", "gage_id": "08164600"},
    "17040": {"source": "usgs", "gage_id": "08164800"},
    "16014": {"source": "usgs", "gage_id": "08164525", "special": "lake_texana"},
    #Guadalupe
    "18015": {"source": "usgs", "gage_id": "08177500"},
    #Mission
    #"20030": {"source": "usgs", "gage_id": "08189800"}, This gaging station has no data from 09/30/1991. Now, this watershed has become an ungaged watershed. 
    "20060": {"source": "usgs", "gage_id": "08189700"},
    "20085": {"source": "usgs", "gage_id": "08189500"},
    "20125": {"source": "usgs", "gage_id": "08189200"},
    #Nueces
    "22010": {"source": "usgs", "gage_id": "08211520"},
    #Laguna_Madre
    #"22033": {"source": "usgs", "gage_id": "08211900"}, This wsd does not exist. The gage is upstream of 22031
    "22042": {"source": "usgs", "gage_id": "08212400"},
    "22911": {"source": "ibwc", "gage_id": "08470200"},
    "22909": {"source": "ibwc", "gage_id": "08470400"}
}


# ---------------------------------------------------------------------
# SYSTEM GAGES:
# gage is upstream of all downstream watersheds, used as a system proxy
#
# schema:
#   proxy_group_name: {
#       "source": "usgs" | "ibwc" | ...,
#       "gage_id": "08116650",
#       "watersheds": ["12001", "12002"],
#       "orders": {"12001": 1, "12002": 2},   # optional ordering downstream
#       "special": None                        # optional
#   }
# ---------------------------------------------------------------------
SYSTEM_GAGES = {
    "sabine": {
        "source": "usgs",
        "gage_id": "08030500",
        "river": "sabine",
        "estuary": "sabine"
    },
     "neches": {
        "source": "usgs",
        "gage_id": "08041000",
        "river": "neches",
        "estuary": "sabine"
    },
     "village_creek": {
        "source": "usgs",
        "gage_id": "08041500",
        "river": "village creek",
        "estuary": "sabine"
    },
    # "trinity": { This station is removed for now since a new gaging station (08067000) has been added downstream for data generation process.
       # "source": "usgs",
       # "gage_id": "08066500",
       # "river": "trinity",
        #"estuary": "galveston"
    #},
    "brazos": {
        "source": "usgs",
        "gage_id": "08116650",
        "river": "brazos",
        "estuary": "brazos"
       # "watersheds": ["12001", "12002"],
        #"orders": {"12001": 1, "12002": 2},
        #"special": None
    },

    "sanbern": {
        "source": "usgs",
        "gage_id": "08117500",
        "river": "san Bernard",
        "estuary": "san Bernard"
        #"watersheds": ["12101", "12102"],
        #"orders": {"12101": 1, "12102": 2},
        #"special": None
    },
    "matagorda": {
        "source": "usgs",
        "gage_id": "08162000",
        "river": "colorado",
        "estuary": "matagorda"
        #"watersheds": ["14010"],
        #"orders": {"14010": 1},
        #"special": "colorado_up"
    },
    #Lake Texana in Matagorda has a usgs gage number but data do not come from usgs
    "nueces": {
        "source": "usgs",
        "gage_id": "08211000",
        "river": "nueces",
        "estuary": "nueces"
        #"watersheds": ["21010", "24820"],
        #"orders": {"21010": 1, "24820":2}, #should we include bay segments?
        #"special": None
    },
    "petronila": {
        "source": "usgs",
        "gage_id": "08212820",
        "river": "petronila",
        "estuary": "laguna madre"

        #"watersheds": ["22021"],
        #"orders": {"22021": 1}, #should we include bay segments?
        #"special": "petronila"
    },
    "san_fernando": {
        "source": "usgs",
        "gage_id": "08211900",
        "river": "san fernando",
        "estuary": "laguna madre"
       # "watersheds": ["22031", "22030"],
        #"orders": {"22031": 1, "22030":2}, #should we include bay segment ?
       # "special": "petronila"
         
    },
    "guadalupe": {
        "source": "usgs",
        "gage_id": "08176500",
        "river": "guadalupe",
        "estuary": "guadalupe"
        #"watersheds": ["18012", "18020", "24601"],
        #"orders": {"18012": 1, "18020": 2, "24601": 3}, #should we include bay segment ?
        #"special": None
    },
    "san_antonio": {
        "source": "usgs",
        "gage_id": "08188500",
        "river": "san antonio",
        "estuary": "guadalupe"

        #"watersheds": ["19012", "24601", "24604"],
        #"orders": {"19012": 1, "24601": 2, "24601": 3}, #should we include bay segment ?
        #"special": None
    },
}

# ---------------------------------------------------------------------
# COMPONENT FLAGS
# These describe what components apply to each watershed
# ---------------------------------------------------------------------
HAS_UNGAGED_WS = {
    #Guadalupe
    "18012", "18020", "19011", "19012", "24601", "24602", "24603", "24604", "24605", "24606", "24607", "24608",
    #Matagorda
    "15010","15030", "15040", "15050", "15060", "16001", "16007", "16008", "17010", "17030", "17050", "17060", "17070",
    #Galveston
    "07050", "07060", "07070", "08010", "08020", "09010", "10002", "10010", "10030", "10040", "10050", "10060", "10062",  "10075", "10080", "10081", "10090", "10091", "10100", "10101", "10110", "10111", "10120", "10130", "11003", "11010", "11020", "11021", "11030", "11040", "11070", "11080", "11092", "11094", "11110", "11122", "11124", "11130", "11150",
    #Mission 
    "20012", "20014", "20020", "20030", "20040", "20050", "20070", "20100", "20110", "20120", "20130", "20140", "20165", "20180", "20192", "20194", 
    #Nueces
    "20005", "21010", "22011", "22012", "22013", "22014", "22015",
    # Sabine
    "05008", "05010", "05012", "06010", "07010", "07020", "07030", "07040",
    #San Bernard
    "12101", "12102", "12103", "12104", "12201",
    #Laguna Madre
    "22020", "22021", "22022", "22023", "22024", "22025", "22026", "22027", "22030", "22031", "22032", "22033", "22040", "22041", "22900", "22901", "22902", "22903", "22904", "22905", "22906", "22907", "22908",
    #Brazos
    "12001", "12002",
    #San Bernard
    "12101", "12102", "12103", "12103", "12104", "12201",
    #East Matagorda
    "13101", "13102", "13103", "13104", "13105", "13106", "13107", "13108", "13109"
}
HAS_BAY_WS = {
    #Guadalupe
    "24609", "24610", "24611", "24612", "24613", "24614",
    #Matagorda
    "15015","15025", "15055", "17065", "17075",
    #Galveston
    "24210", "24220", "24230", "24235", "24240", "24245", "24250", "24260", "24320", "24390",
    #Mission 
    "24710", "24711", "24720", "24730", "24831",
    #Nueces
    "24810", "24811", "24820", "24830", "24850",
    # Sabine
    "24120", 
    #San Bernard
    "24408", "24409", 
    #Laguna Madre
    "22028", "22910",
    #East Matagorda
    "24410"
}

HAS_DIVERSION_WS = {
    "5010", "5008",
    #sabine 
    "05030","05008","05010","05012","05110","06010","06020","06070","06080","07010","07020","07030","07040","24120",
#galveston 
    "07050","07060","07070","08010","08020","08110","09010","09030","10002","10010","10020","10030","10040",
          "10050","10060","10061","10062","10063","10064","10065","10066","10073","10074","10075",
          "10080","10081","10090","10091","10100","10101","10110","10111","10120","11003","11010",
          "11020","11021","11030","11040","11070","11080","11081","11092","11094","11110","11122",
          "11124","11130","11150","24210","24220","24230","24235","24240","24245","24250","24260","24320","24390",
#brazos 
    "12001","12002",
#san_bernard
    "12101","12102","12103","12104","12201","24408","24409",
#east_matagorda 
    "13101","13102","13103","13104","13105","13106","13107","13108","13109","24410",
#matagorda 
    "14010","15010","15020","15030","15040","15050","15060","16001","16005","16007","16008",
             "16014","17010","17020","17030","17040","17050","17060","17070","15015","15025","15055",
             "17065","17075",
#guadalupe 
    "18012","18015","18018","18020","19011","19012","24601","24602","24603","24604","24605",
             "24606","24607","24608","24609","24610","24611","24612","24613","24614",
#mission 
    "20012","20014","20020","20030","20040","20050","20060","20070","20085","20100","20110",
           "20120","20125","20130","20140","20165","20180","20192","20194","24710","24711","24720",
           "24730","24831",
#nueces 
    "20005","21010","22010","22011","22012","22013","22014","22015","24810","24811","24820",
          "24830","24850",
#laguna_madre 
    "22020","22021","22022","22023",'22024',"22025","22026","22027","22030","22031",
                "22032","22033","22040","22041","22042","22900","22901","22902","22903","22904",
                "22905","22906","22907","22908","22909","22911","22028","22910"

}

HAS_RETURN_WS = {
    #sabine
    "05030","05008","05010","05012","05110","06010","06020","06070","06080","07010","07020","07030","07040","24120",
    #galveston
    "07050","07060","07070","08010","08020","08110","09010","09030","10002","10010","10020","10030","10040",
          "10050","10060","10061","10062","10063","10064","10065","10066","10073","10074","10075",
          "10080","10081","10090","10091","10100","10101","10110","10111","10120","11003","11010",
          "11020","11021","11030","11040","11070","11080","11081","11092","11094","11110","11122",
          "11124","11130","11150","24210","24220","24230","24235","24240","24245","24250","24260","24320","24390",
    
    #brazos 
    "12001","12002",
    #san_bernard
    "12101","12102","12103","12104","12201","24408","24409",
    #east_matagorda
    "13101","13102","13103","13104","13105","13106","13107","13108","13109","24410",
    #matagorda 
    "14010","15010","15020","15030","15040","15050","15060","16001","16005","16007","16008",
             "16014","17010","17020","17030","17040","17050","17060","17070","15015","15025","15055",
             "17065","17075",
    #guadalupe
    "18012","18015","18018","18020","19011","19012","24601","24602","24603","24604","24605",
             "24606","24607","24608","24609","24610","24611","24612","24613","24614",
    #mission 
    "20012","20014","20020","20030","20040","20050","20060","20070","20085","20100","20110",
           "20120","20125","20130","20140","20165","20180","20192","20194","24710","24711","24720",
           "24730","24831",
    #nueces 
    "20005","21010","22010","22011","22012","22013","22014","22015","24810","24811","24820",
          "24830","24850",
    #laguna_madre 
    "22020","22021","22022","22023","22024","22025","22026","22027","22030","22031",
                "22032","22033","22040","22041","22042","22900","22901","22902","22903","22904",
                "22905","22906","22907","22908","22909","22911","22028","22910"
}
#add bay watersheds

# ============================================================
# HELPERS
# ============================================================

FIELD_DEFAULTS = {
    #"ESTUARY": None,
   # "RIVER": None,
    #"IS_TERMIN": 0,
    "HAS_GAGED": 0,
    #"G_SCOPE": None,     # local / system
    "G_SOURCE": None,    # usgs / ibwc / lnra / special
    "GAGE_ID": None,
    #"G_PROXY_G": None,
    #"PXY_ORDER": None,
    "SPECIAL_T": None,
    "HAS_UNGAG": 0,
    "HAS_BAY": 0,
    #"HAS_DIVERT": 0,
    #"HAS_RETURN": 0,
}

SHORT_TEXT_FIELDS = [
     "G_SOURCE", "GAGE_ID", "SPECIAL_T"
]

INT_FIELDS = [
   "HAS_GAGED", "HAS_UNGAG", "HAS_BAY"#, "HAS_DIVERT", "HAS_RETURN"
]


def ensure_fields(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Ensure all required output fields exist."""
    for field, default in FIELD_DEFAULTS.items():
        if field not in gdf.columns:
            gdf[field] = default
    return gdf


def normalize_ws_ids(gdf: gpd.GeoDataFrame, ws_field="WS_ID") -> gpd.GeoDataFrame:
    """Normalize WS_ID to stripped string for matching."""
    gdf[ws_field] = gdf[ws_field].astype(str).str.strip().str.zfill(5)
    return gdf


def apply_defaults(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reset managed fields to defaults before rebuilding."""
    for field, default in FIELD_DEFAULTS.items():
        gdf[field] = default
    return gdf


def assign_component_flags(gdf: gpd.GeoDataFrame, ws_field="WS_ID") -> gpd.GeoDataFrame:
    """Populate HAS_UNGAG / HAS_DIVERT / HAS_RETURN."""
    gdf["HAS_UNGAG"] = gdf[ws_field].isin(HAS_UNGAGED_WS).astype(int)
    #gdf["HAS_DIVERT"] = gdf[ws_field].isin(HAS_DIVERSION_WS).astype(int)
    #gdf["HAS_RETURN"] = gdf[ws_field].isin(HAS_RETURN_WS).astype(int)
    gdf["HAS_BAY"] = gdf[ws_field].isin(HAS_BAY_WS).astype(int)
    return gdf


# def assign_basic_metadata(gdf: gpd.GeoDataFrame, ws_field="WS_ID") -> gpd.GeoDataFrame:
#     """Populate ESTUARY / RIVER / IS_TERMIN."""
#     gdf["ESTUARY"] = gdf[ws_field].map(ESTUARY_MAP)
#     gdf["RIVER"] = gdf[ws_field].map(RIVER_MAP)
#     gdf["IS_TERMIN"] = gdf[ws_field].isin(TERMINAL_WS).astype(int)
#     return gdf


def assign_local_gages(gdf: gpd.GeoDataFrame, ws_field="WS_ID") -> gpd.GeoDataFrame:
    """Populate local gage attributes."""
    for ws_id, meta in LOCAL_GAGES.items():
        idx = gdf[ws_field] == ws_id
        if idx.any():
            gdf.loc[idx, "HAS_GAGED"] = 1
            #gdf.loc[idx, "G_SCOPE"] = "local"
            gdf.loc[idx, "G_SOURCE"] = meta.get("source")
            gdf.loc[idx, "GAGE_ID"] = meta.get("gage_id")
            gdf.loc[idx, "SPECIAL_T"] = meta.get("special")
            #gdf.loc[idx, "G_PROXY_G"] = None
            #gdf.loc[idx, "PXY_ORDER"] = None
    return gdf

def validate_registry(gdf: gpd.GeoDataFrame, ws_field="WS_ID") -> None:
    """Sanity checks for watershed registry."""
    # Missing ESTUARY
    missing_est = gdf[gdf["Estuary"].isna()]
    if not missing_est.empty:
        print("WARNING: Some watersheds are missing Estuary:")
        print(missing_est[[ws_field]].head(20))

    # Local gage rows without source/id
    bad_gaged = gdf[(gdf["HAS_GAGED"] == 1) & ((gdf["G_SOURCE"].isna()) | (gdf["GAGE_ID"].isna()))]
    if not bad_gaged.empty:
        print("WARNING: Some HAS_GAGED rows are missing G_SOURCE or GAGE_ID:")
        print(bad_gaged[[ws_field, "G_SOURCE", "GAGE_ID"]])

    print("\nRegistry summary")
    print("----------------")
    print("Total watersheds:", len(gdf))
    print("Has gaged:", int(gdf["HAS_GAGED"].sum()))
    print("Has ungaged:", int(gdf["HAS_UNGAG"].sum()))
    print("Has bay:", int(gdf["HAS_BAY"].sum()))
    #print("Has diversion:", int(gdf["HAS_DIVERT"].sum()))
    #print("Has return:", int(gdf["HAS_RETURN"].sum()))

    print("\nLocal gage sources:")
    print(gdf["G_SOURCE"].value_counts(dropna=False))


def coerce_types(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Make output fields shapefile-friendly."""
    for field in SHORT_TEXT_FIELDS:
        gdf[field] = gdf[field].where(gdf[field].notna(), None)

    for field in INT_FIELDS:
        gdf[field] = pd.to_numeric(gdf[field], errors="coerce").fillna(0).astype(int)

    return gdf


# ============================================================
# MAIN
# ============================================================

def main():
    in_path = Path(INPUT_SHP)
    out_path = Path(OUTPUT_SHP)

    if not in_path.exists():
        raise FileNotFoundError(f"Input shapefile not found: {in_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    gdf = gpd.read_file(in_path)
    print(gdf.columns)
    if "WS_ID" not in gdf.columns:
        raise ValueError("Input shapefile must contain a WS_ID field.")

    gdf = ensure_fields(gdf)
    gdf = normalize_ws_ids(gdf, ws_field="WS_ID")
    gdf = apply_defaults(gdf)

    # Populate fields
    #gdf = assign_basic_metadata(gdf, ws_field="WS_ID")
    gdf = assign_component_flags(gdf, ws_field="WS_ID")
    gdf = assign_local_gages(gdf, ws_field="WS_ID")
    #gdf = assign_upstream_gages(gdf, ws_field="WS_ID")
    gdf = coerce_types(gdf)

    # Validate
    validate_registry(gdf, ws_field="WS_ID")

    # Write output
    gdf.to_file(out_path)
    print(f"\nUpdated registry shapefile written to:\n{out_path}")


if __name__ == "__main__":
    main()
