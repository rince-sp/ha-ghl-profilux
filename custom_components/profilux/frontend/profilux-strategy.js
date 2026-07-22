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

    const isSensor = (id) => id.startsWith("sensor.");
    const gauges = ids.filter(
      (id) => isSensor(id) && !/_(current|power|status)$/.test(id) && !/_fill_level$/.test(id)
    );
    const dosing = ids.filter((id) => isSensor(id) && /_fill_level$/.test(id));
    // Power/current: totals first, then per-socket currents.
    const power = ids
      .filter((id) => isSensor(id) && /_(current|power)$/.test(id))
      .sort((a, b) => (b.includes("total") ? 1 : 0) - (a.includes("total") ? 1 : 0));
    const totalPower = ids.find((id) => id.endsWith("_total_power"));
    const status = ids.filter((id) => isSensor(id) && id.endsWith("_status"));
    const alarms = ids.filter(
      (id) => id.startsWith("binary_sensor.") && id.endsWith("_alarm")
    );
    const floats = ids.filter(
      (id) => id.startsWith("binary_sensor.") && /_(min|max)_float$/.test(id)
    );
    const sockets = ids.filter(
      (id) =>
        id.startsWith("binary_sensor.") &&
        !id.endsWith("_alarm") &&
        !/_(min|max)_float$/.test(id)
    );

    const totalCurrent = ids.find((id) => id.endsWith("_total_current"));
    const socketCurrents = power.filter((id) => !id.includes("total"));

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
      color: "amber",
      grid_options: { columns: 4 },
    });
    const tile = (id, columns, extra) => ({
      type: "tile",
      entity: id,
      grid_options: { columns },
      ...(extra || {}),
    });
    const heading = (heading, icon) => ({ type: "heading", heading, icon });
    const subheading = (heading) => ({ type: "heading", heading_style: "subtitle", heading });

    const sections = [];

    // Sensors — gauges, two per row.
    if (gauges.length) {
      sections.push({
        type: "grid",
        cards: [heading("Sensoren", "mdi:gauge"), ...gauges.map(gaugeCard)],
      });
    }

    // Power & current — totals up top (side by side), 24 h trend, per-socket draw.
    if (power.length) {
      const cards = [heading("Leistung & Stromaufnahme", "mdi:flash")];
      if (totalPower) cards.push(tile(totalPower, 6, { color: "orange", icon: "mdi:flash" }));
      if (totalCurrent) cards.push(tile(totalCurrent, 6, { color: "orange", icon: "mdi:current-ac" }));
      if (totalPower) {
        cards.push({
          type: "history-graph",
          hours_to_show: 24,
          entities: [{ entity: totalPower }],
          grid_options: { columns: "full", rows: 4 },
        });
      }
      if (socketCurrents.length) {
        cards.push(subheading("Pro Steckdose"));
        cards.push(...socketCurrents.map((id) => tile(id, 4)));
      }
      sections.push({ type: "grid", cards });
    }

    // Switching channels — outlet tiles.
    if (sockets.length) {
      sections.push({
        type: "grid",
        cards: [heading("Schaltkanäle", "mdi:power-socket-de"), ...sockets.map(socketCard)],
      });
    }

    // Dosing pumps — reservoir fill level.
    if (dosing.length) {
      sections.push({
        type: "grid",
        cards: [
          heading("Dosierpumpen", "mdi:cup-water"),
          ...dosing.map((id) => tile(id, 6, {
            color: "light-blue",
            icon: "mdi:cup-water",
            state_content: ["state", "percent"],
          })),
        ],
      });
    }

    // Level loops — per loop: status, then its min/max float switches; alarms last.
    if (status.length || alarms.length || floats.length) {
      const cards = [heading("Niveau & Alarm", "mdi:water-percent")];
      for (const st of status) {
        const stem = st.replace(/_status$/, "");
        cards.push(tile(st, 12, { icon: "mdi:waves" }));
        for (const f of floats.filter((id) => id.includes(stem.replace(/^sensor\./, "")))) {
          cards.push(tile(f, 6));
        }
      }
      // Any floats we couldn't pair to a status, then the alarms.
      const paired = new Set();
      for (const st of status) {
        const key = st.replace(/^sensor\./, "").replace(/_status$/, "");
        floats.filter((id) => id.includes(key)).forEach((id) => paired.add(id));
      }
      cards.push(...floats.filter((id) => !paired.has(id)).map((id) => tile(id, 6)));
      cards.push(...alarms.map((id) => tile(id, 6, { icon: "mdi:alert" })));
      sections.push({ type: "grid", cards });
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

// Guard so loading this module twice (e.g. as a frontend extra *and* a Lovelace
// resource) doesn't throw "already defined".
if (!customElements.get("ll-strategy-dashboard-profilux")) {
  customElements.define("ll-strategy-dashboard-profilux", ProfiluxDashboardStrategy);
}
