"""Constants for the ProfiLux integration."""

DOMAIN = "profilux"

# Polling interval in seconds. Each poll opens one short conversation and reads
# every sensor + socket, so keep it gentle on the controller.
SCAN_INTERVAL = 60

MANUFACTURER = "GHL"

CONF_INTERFACE = "interface"

# Port for the GHL API (text protocol) interface. Ignored by the SWMBus
# (websocket/http) interfaces, which use their own fixed paths.
CONF_PORT = "port"
DEFAULT_API_PORT = 10002

# Opt-in socket control. Off by default: enabling it exposes a switch per socket
# that *writes* to the controller (forcing "always on" / "always off"), which
# can override the automatic control of live aquarium equipment.
CONF_CONTROL_SOCKETS = "control_sockets"
DEFAULT_CONTROL_SOCKETS = False

# Opt-in GHL API control. Off by default: enabling it exposes entities that
# *write* to the controller over the GHL API — editable setpoints, feed pause /
# water change / maintenance, light scenes, brightness and measurement triggers.
# Requires the API to be set to "full access" on the controller (not read only).
CONF_API_CONTROL = "api_control"
DEFAULT_API_CONTROL = False

# The powerbar reports current (A); power (W) is estimated as current × mains
# voltage. EU default; adjust if your mains differs.
MAINS_VOLTAGE = 230
