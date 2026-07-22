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
    const socketSensors = ids.filter(
      (id) =>
        id.startsWith("binary_sensor.") &&
        !id.endsWith("_alarm") &&
        !/_(min|max)_float$/.test(id)
    );
    // Prefer the controllable switch entity (tap-to-toggle) over the read-only
    // status sensor when socket control is enabled and a switch exists. Match by
    // the socket name (everything after the domain + "…profilux_"), because the
    // switch and binary_sensor can carry different entity-id prefixes (e.g. one
    // created before the device gained an area name).
    const socketName = (id) => id.replace(/^.*?profilux_/, "");
    // The controller-wide alarm's name is just "alarm"; the per-loop alarms are
    // "<loop>_alarm". Pull the controller alarm out to pin it at the top.
    const controllerAlarm = alarms.find((id) => socketName(id) === "alarm");
    const levelAlarms = alarms.filter((id) => id !== controllerAlarm);
    const switches = ids.filter((id) => id.startsWith("switch."));
    const sockets = socketSensors.map((bs) => {
      const name = socketName(bs);
      const sw = switches.find((s) => socketName(s) === name);
      return sw || bs;
    });

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
    // Tapping a socket opens its more-info dialog (toggle + the current/power
    // attributes on the switch); the icon still toggles for a quick switch.
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

    // Controller alarm — pinned at the very top of the dashboard.
    if (controllerAlarm) {
      sections.push({
        type: "grid",
        cards: [
          {
            type: "tile",
            entity: controllerAlarm,
            name: "Controller Alarm",
            icon: "mdi:alert",
            color: "red",
            grid_options: { columns: "full" },
          },
        ],
      });
    }

    // Sensors — gauges, two per row.
    if (gauges.length) {
      sections.push({
        type: "grid",
        cards: [heading("Sensoren", "mdi:gauge"), ...gauges.map(gaugeCard)],
      });
    }

    // Power & current — overall draw only. Per-socket current lives in each
    // socket's more-info dialog (as switch attributes), not on the main page.
    if (totalPower || totalCurrent) {
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

    // Level loops — per loop: status, its min/max floats, then its alarm. Match
    // floats/alarms to a loop by name (robust to differing id prefixes).
    if (status.length || levelAlarms.length || floats.length) {
      const cards = [heading("Niveau & Alarm", "mdi:water-percent")];
      const loopName = (id) =>
        socketName(id).replace(/_(status|alarm|min_float|max_float)$/, "");
      const paired = new Set();
      for (const st of status) {
        const loop = loopName(st);
        cards.push(tile(st, 12, { icon: "mdi:waves" }));
        for (const f of floats.filter((id) => loopName(id) === loop)) {
          cards.push(tile(f, 6));
          paired.add(f);
        }
        for (const a of levelAlarms.filter((id) => loopName(id) === loop)) {
          cards.push(tile(a, 6, { icon: "mdi:water-alert" }));
          paired.add(a);
        }
      }
      // Any floats/alarms not tied to a status loop.
      cards.push(
        ...[...floats, ...levelAlarms]
          .filter((id) => !paired.has(id))
          .map((id) => tile(id, 6))
      );
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
