import requests
import certifi
import pandas as pd
urls <- ["https://www.waterdatafortexas.org/coastal/api/hydrology/geography/689/timeseries?output_format=csv&resample=month",
          "https://www.waterdatafortexas.org/coastal/api/hydrology/geography/690/timeseries?output_format=csv&resample=month",
          "https://www.waterdatafortexas.org/coastal/api/hydrology/geography/691/timeseries?output_format=csv&resample=month",
          "https://www.waterdatafortexas.org/coastal/api/hydrology/geography/692/timeseries?output_format=csv&resample=month",
          "https://www.waterdatafortexas.org/coastal/api/hydrology/geography/693/timeseries?output_format=csv&resample=month",
          "https://www.waterdatafortexas.org/coastal/api/hydrology/geography/694/timeseries?output_format=csv&resample=month",
          "https://www.waterdatafortexas.org/coastal/api/hydrology/geography/698/timeseries?output_format=csv&resample=month",
          "https://www.waterdatafortexas.org/coastal/api/hydrology/geography/695/timeseries?output_format=csv&resample=month",
          "https://www.waterdatafortexas.org/coastal/api/hydrology/geography/696/timeseries?output_format=csv&resample=month",
          "https://www.waterdatafortexas.org/coastal/api/hydrology/geography/697/timeseries?output_format=csv&resample=month"]
#Estuary name in order of their urls
est_name <- {"Sabine-Neches","Galveston","Brazos", "San Bernard","East Matagorda","Matagorda","Guadalupe","Mission-Aransas","Nueces","Laguna Madre"}

dfs = []

for url, estuary in zip(urls, est_names):

    r = requests.get(
        url,
        timeout=300,
        verify=certifi.where()
    )
    r.raise_for_status()

    # Read CSV into DataFrame
    df = pd.read_csv(pd.io.common.StringIO(r.text))

    # Add estuary name
    df["Estuary"] = estuary

    dfs.append(df)

# Combine all estuaries
df_all = pd.concat(dfs, ignore_index=True)
