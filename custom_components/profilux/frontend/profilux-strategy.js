/*
 * ProfiLux dashboard strategy.
 *
 * Auto-generates a dashboard from whatever ProfiLux entities exist — no
 * hard-coded entity IDs. Registered as a frontend resource by the integration,
 * so a dashboard needs only:
 *
 *   strategy:
 *     type: custom:profilux
 *
 * Sensors become gauges, power sockets become outlet tiles, per-socket current
 * sensors get their own row, and level/alarm binary sensors get a status row.
 */

// Gauge ranges keyed by unit of measurement.
const RANGES = {
  "°C": { min: 18, max: 32, severity: { green: 24, yellow: 27, red: 29 } },
  pH: { min: 6, max: 9 },
  mV: { min: 0, max: 500 },
  "mS/cm": { min: 0, max: 60, severity: { green: 45, yellow: 56, red: 58 } },
  "µS/cm": { min: 0, max: 2000 },
  "%": { min: 0, max: 100 },
  "mg/L": { min: 0, max: 20 },
};

class ProfiluxDashboardStrategy {
  static async generate(config, hass) {
    const entities = Object.values(hass.entities || {}).filter(
      (e) => e.platform === "profilux"
    );
    const ids = entities.map((e) => e.entity_id).sort((a, b) => a.localeCompare(b));
    const stateOf = (id) => hass.states[id];

    const gauges = ids.filter(
      (id) => id.startsWith("sensor.") && !id.endsWith("_current")
    );
    const currents = ids.filter((id) => id.endsWith("_current"));
    const alarms = ids.filter(
      (id) => id.startsWith("binary_sensor.") && id.endsWith("_alarm")
    );
    const sockets = ids.filter(
      (id) => id.startsWith("binary_sensor.") && !id.endsWith("_alarm")
    );

    const gaugeCard = (id) => {
      const unit = stateOf(id) ? stateOf(id).attributes.unit_of_measurement : undefined;
      const r = RANGES[unit] || {};
      const card = { type: "gauge", entity: id, needle: true, grid_options: { columns: 6 } };
      if (r.min !== undefined) card.min = r.min;
      if (r.max !== undefined) card.max = r.max;
      if (r.severity) card.severity = r.severity;
      if (unit) card.unit = unit;
      return card;
    };
    const socketCard = (id) => ({
      type: "tile",
      entity: id,
      icon: "mdi:power-socket-de",
      grid_options: { columns: 4 },
    });
    const tile = (id, columns) => ({ type: "tile", entity: id, grid_options: { columns } });
    const heading = (heading, icon) => ({ type: "heading", heading, icon });

    const sections = [];
    if (gauges.length) {
      sections.push({ type: "grid", cards: [heading("Sensoren", "mdi:gauge"), ...gauges.map(gaugeCard)] });
    }
    if (sockets.length) {
      sections.push({
        type: "grid",
        cards: [heading("Schaltkanäle", "mdi:power-socket-de"), ...sockets.map(socketCard)],
      });
    }
    if (currents.length) {
      sections.push({
        type: "grid",
        cards: [heading("Stromaufnahme", "mdi:flash"), ...currents.map((id) => tile(id, 4))],
      });
    }
    if (alarms.length) {
      sections.push({
        type: "grid",
        cards: [heading("Niveau & Alarm", "mdi:water-percent"), ...alarms.map((id) => tile(id, 6))],
      });
    }

    return {
      title: config.title || "ProfiLux",
      views: [
        {
          title: "Aquarium",
          path: "aquarium",
          type: "sections",
          icon: "mdi:fishbowl",
          max_columns: 3,
          sections,
        },
      ],
    };
  }
}

customElements.define("ll-strategy-dashboard-profilux", ProfiluxDashboardStrategy);
