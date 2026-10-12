#!/usr/bin/env bash
# Fetch the public benchmark datasets into data/raw/<id>/ (one fresh directory per source), then write data/MANIFEST.sha256.
# Idempotent: existing files are kept. Sources and licences are recorded in data/catalog.toml and docs/DATA.md.
set -euo pipefail
# usage: fetch_data.sh [dev|all]   dev = only the datasets the search loop needs (ETT, Weather, Exchange); all = every tier
TIER="${1:-all}"
cd "$(dirname "$0")/.."
RAW=data/raw; mkdir -p "$RAW"
get() {  # id url filename
  local d="$RAW/$1"; mkdir -p "$d"
  [ -s "$d/$3" ] && { echo "have  $1/$3"; return; }
  echo "fetch $1/$3"; curl -fL --retry 3 --max-time 600 -sS -o "$d/$3.part" "$2" && mv "$d/$3.part" "$d/$3"
}
E=https://raw.githubusercontent.com/zhouhaoyi/ETDataset/main/ETT-small
L=https://raw.githubusercontent.com/laiguokun/multivariate-time-series-data/master
W=https://www.bgc-jena.mpg.de/wetter/data
M=https://raw.githubusercontent.com/wayne155/multivariate_timeseries_datasets/main
get etth1 $E/ETTh1.csv ETTh1.csv;           get ettm1 $E/ETTm1.csv ETTm1.csv
get exchange $L/exchange_rate/exchange_rate.txt.gz exchange_rate.txt.gz
get weather_primary $W/mpi_roof_2020a.zip mpi_roof_2020a.zip; get weather_primary $W/mpi_roof_2020b.zip mpi_roof_2020b.zip
if [ "$TIER" = "dev" ]; then ( cd data && find raw -type f ! -name '*.part' | sort | xargs shasum -a 256 > MANIFEST.dev.sha256 ); echo "dev tier: $(du -sh data/raw | cut -f1)"; exit 0; fi
get etth2 $E/ETTh2.csv ETTh2.csv;           get ettm2 $E/ETTm2.csv ETTm2.csv
get electricity $L/electricity/electricity.txt.gz electricity.txt.gz
get traffic $L/traffic/traffic.txt.gz traffic.txt.gz
get solar $L/solar-energy/solar_AL.txt.gz solar_AL.txt.gz
get weather_primary $W/mpi_roof_2020a.zip mpi_roof_2020a.zip
get weather_primary $W/mpi_roof_2020b.zip mpi_roof_2020b.zip
if [ "$TIER" = "dev" ]; then
  # dev tier: ETTh1, ETTm1, Weather, Exchange only
  :
fi
get weather_mirror $M/weather.zip weather.zip     # cross-check copy of the LTSF 'weather.csv'
get illness_mirror $M/illness.zip illness.zip     # CDC FluView ILI, LTSF version (md5 e18542fafd509ad52ffedde1b0f1018d)
# ---- tier 2: extended, non-LTSF sources (independent families for confirmation / sealed / stress roles)
U=https://archive.ics.uci.edu/static/public
get uci_airquality $U/360/air+quality.zip air_quality.zip
get uci_bike $U/275/bike+sharing+dataset.zip bike_sharing.zip
get uci_appliances $U/374/appliances+energy+prediction.zip appliances.zip
get uci_household $U/235/individual+household+electric+power+consumption.zip household_power.zip
get uci_metro $U/492/metro+interstate+traffic+volume.zip metro_traffic.zip
get sunspot_daily https://www.sidc.be/SILSO/DATA/SN_d_tot_V2.0.csv SN_d_tot_V2.0.csv
get sunspot_monthly https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.csv SN_m_tot_V2.0.csv
get m4_hourly https://raw.githubusercontent.com/Mcompetitions/M4-methods/master/Dataset/Train/Hourly-train.csv Hourly-train.csv
get fred_cpi "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL" CPIAUCSL.csv
get melbourne_temp https://raw.githubusercontent.com/jbrownlee/Datasets/master/daily-min-temperatures.csv daily-min-temperatures.csv
get airline https://raw.githubusercontent.com/jbrownlee/Datasets/master/airline-passengers.csv airline-passengers.csv
# ---- tier 3: Hugging Face autogluon/chronos_datasets (curated Monash/GluonTS/etc. collection; per-dataset licences in its README)
HF=https://huggingface.co/datasets/autogluon/chronos_datasets/resolve/main
get chronos_readme $HF/README.md README.md
for d in monash_saugeenday monash_pedestrian_counts monash_australian_electricity monash_covid_deaths monash_kdd_cup_2018 monash_hospital \
         monash_nn5_weekly monash_tourism_monthly wind_farms_hourly ercot uber_tlc_hourly taxi_30min monash_car_parts monash_rideshare \
         monash_fred_md monash_electricity_weekly; do
  get "chronos_$d" "$HF/$d/train-00000-of-00001.parquet" "$d.parquet"
done
( cd data && find raw -type f ! -name '*.part' | sort | xargs shasum -a 256 > MANIFEST.sha256 )
echo "manifest: $(wc -l < data/MANIFEST.sha256) files, $(du -sh data/raw | cut -f1)"
