#!/usr/bin/env bash
# Cut one Danish Maritime Authority AIS day (http://aisdata.ais.dk) down to Class A ships in the
# western Baltic, one fix per ship per minute. Usage: extract_dk.sh aisdk-2024-11-18.zip out.csv
set -euo pipefail
unzip -p "$1" | awk -F, 'NR > 1 && $2 == "Class A" && $4 > 53.5 && $4 < 60 && $5 > 9.5 && $5 < 21 {
  m = substr($1, 1, 16)
  if (last[$3] != m) { last[$3] = m; print $1 "," $3 "," $4 "," $5 "," $6 "," $8 "," $9 "," $11 "," $13 "," $14 "," $17 }
}' > "$2"
