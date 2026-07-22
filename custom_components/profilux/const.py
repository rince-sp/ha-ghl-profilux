"""Constants for the ProfiLux integration."""

DOMAIN = "profilux"

# Polling interval in seconds. Each poll opens one short conversation and reads
# every sensor + socket, so keep it gentle on the controller.
SCAN_INTERVAL = 60

MANUFACTURER = "GHL"

CONF_INTERFACE = "interface"

# The powerbar reports current (A); power (W) is estimated as current × mains
# voltage. EU default; adjust if your mains differs.
MAINS_VOLTAGE = 230
