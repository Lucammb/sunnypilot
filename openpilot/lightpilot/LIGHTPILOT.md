
## Fase 2 — anticipazione eventi e limite di velocità da navigazione

- `navd` scrive ogni secondo `/dev/shm/lightpilot_zones.json` con le zone davanti all'auto (rotonde, svolte, stop, semafori, uscite) in metri lungo il percorso. File più vecchio di 3 s = ignorato.
- `NavSpeedCap` (in `LongitudinalPlannerSP`) può solo ridurre la velocità obiettivo: profilo cinematico v = sqrt(vt² + 2·a·(d−8)), tenuta in zona, rilasciata 5 m dopo. L'acceleratore lo sovrascrive sempre.
- Configurazione in `/data/lightpilot/config.json`: `{"nav_speed_cap": {"enabled": true, "roundabout_kph": 30, "turn_kph": 25, "stop_kph": 25, "signal_kph": 0, "decel": 0.45}}`.
- Precedenza in rotonda: l'auto frena con decisione (`roundabout_decel` 0.9 m/s²) e tardi, a ~15 km/h (`roundabout_yield_kph`) 8 m prima dell'ingresso, e resta lì finché non tocchi l'acceleratore (entro 60 m dall'ingresso o dentro l'anello): poi percorre la rotonda alla sua velocità. NON rileva le auto nell'anello: decidi tu quando è libero. `roundabout_yield_kph: 0` disattiva l'attesa. Nessun avviso a schermo.
- Le uscite (`off ramp`/`fork`) non limitano la velocità.
- Requisito: il limite frena davvero solo con controllo longitudinale openpilot (alpha long) su Tesla; con il TACC di serie non ha effetto.
