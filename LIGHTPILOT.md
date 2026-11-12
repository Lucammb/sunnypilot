# lightpilot

Fork personale di [sunnypilot](https://github.com/sunnypilot/sunnypilot) (ramo `staging`), per una Tesla Model 3 HW3
con Konik A1m. Il nome è un omaggio a Luce. Si guida sempre sotto supervisione: velocità impostata bassa, gas manuale per
andare oltre, il conducente resta responsabile.

Base: sunnypilot `staging` v2026.003.000, commit `9a6b7b89c9` (2026-10-20).

## Politica di aggiornamento

- Nessun aggiornamento automatico: `DisableUpdates` va tenuto attivo sul dispositivo.
- Una volta al mese si guardano le novità di sunnypilot e si riportano qui quelle utili (merge o cherry-pick), poi si
  prova e si aggiorna il dispositivo a mano.
- Le modifiche di lightpilot stanno in `openpilot/lightpilot/` e in pochi punti marcati con il commento `lightpilot:`,
  per rendere i merge semplici. Solo Python: nessuna nuova chiave params, nessun rebuild.

## Modifiche rispetto a sunnypilot

### Fase 1 — navigazione
- `navd` legge il token Mapbox da `$MAPBOX_TOKEN` oppure da `/data/lightpilot/mapbox_token` (una riga, `pk....`).
- `navd` non cancella più il percorso quando una richiesta fallisce per la rete; se c'è una destinazione nuova la riprova.
- `python3 -m openpilot.lightpilot.set_destination "lat,lon" | "indirizzo" | --clear` imposta la destinazione via SSH.
  `NavDestination` viene cancellata a ogni avvio e a ogni passaggio offroad: va reimpostata dopo ogni accensione.

### Fase 2 — anticipazione eventi (vedi anche `openpilot/lightpilot/LIGHTPILOT.md`)
- Tetto di velocità prima di rotonde, svolte e stop (`speed_cap.py`); solo frena, il gas lo supera.
- **Svolta automatica da navigazione** (`nav_turn.py`, spenta di default): a una svolta del percorso, senza freccia, sotto
  la velocità `LaneTurnValue` e con `LaneTurnDesire` attivo, il controller delle svolte (`lane_turn_desire.py`) riceve il
  lato dal percorso. La tua freccia ha sempre la precedenza; l'angolo cieco la blocca. Rotonde (opzionali): 1ª uscita =
  destra, 2ª = dritto, 3ª o oltre = sinistra. Si accende in `/data/lightpilot/config.json`:
  `{"nav_turn": {"enabled": true, "window_m": 25, "roundabout": false}}` (vale entro pochi secondi, senza riavvio).
- **Uscite di tangenziale e autostrada** (`nav_exit.py`): avviso "tieniti a destra" sul Konik e sul telefono quando la
  prossima manovra è un'uscita entro 1500 m. Velocità: sulle corsie di marcia solo fino a 80 km/h (mai meno) e solo vicino
  all'uscita (ultimi ~600 m); sotto gli 80, fino alla velocità di svincolo (60 km/h), solo dopo il punto in cui lo svincolo
  si stacca dalla strada e solo se hai messo la freccia dal lato dell'uscita negli ultimi 400 m (2,5 s, o ancora accesa
  all'inizio dello svincolo). Senza freccia resta a 80 e 30 m dopo
  l'uscita il limite sparisce. Nessun cambio corsia automatico. Opzioni in `/data/lightpilot/config.json`:
  `{"nav_exit": {"enabled": true, "approach_kph": 80, "approach_m": 600, "ramp_kph": 60, "advise_m": 1500}}`
  (`approach_kph` non scende sotto 80).
- **Stock / sperimentale** (`drive_mode.py`): il software di base fa sempre partecipare il piano di velocità del modello
  (semafori, stop). Qui torna una scelta. Senza percorso: tocco sul pulsante in alto a destra (`STOCK` / `EXP`), scelta
  salvata in `/data/lightpilot/drive_mode`. Con un percorso: automatico (`AUTO`), sperimentale quando una rotonda, una
  svolta, uno stop o un semaforo del percorso è entro `max(150 m, 12 s)` e fino a 15 m dopo; altrove stock. Non lascia
  mai lo sperimentale da solo sotto 8 m/s mentre è il modello a frenare (auto ferma al rosso). Un semaforo che Mapbox
  non conosce NON viene gestito in automatico. Se i dati del percorso arrivano in ritardo (navd occupato a ricalcolare,
  rete lenta) l'ultima decisione automatica resta valida fino a 15 s, invece di tornare alla scelta manuale. Il bottone
  dice cosa sta guidando (`AUTO EXP` / `AUTO STOCK`): la camera grandangolare dipende solo dalla velocità, non dalla
  modalità. Ogni decisione è scritta in `/data/lightpilot/mode.log` (`tail -f /data/lightpilot/mode.log`). Opzioni in `config.json`:
  `{"drive_mode": {"auto_in_navigation": true, "ahead_s": 12, "min_ahead_m": 150}}`.

### Prossime fasi
2. Avviso corsia per uscite/bivi; poi deceleriamo verso l'uscita dopo un cambio corsia manuale.
3. App di destinazione su navigazione.lucabargigia.it, con il dispositivo che interroga il server.

## Installazione sul Konik

Solo Python, il ramo `staging` ha già i binari: niente compilazione.
1. Sul Mac: fork `Lucammb/sunnypilot`, applica le patch su un ramo `lightpilot` (`git am`) e fai push.
2. Sul Konik: Impostazioni → Sviluppatore → *Installa software personalizzato* → `installer.comma.ai/Lucammb/lightpilot`
   (oppure via SSH: `cd /data/openpilot && git fetch <fork> lightpilot && git checkout FETCH_HEAD`), poi riavvio.
3. Token Mapbox: `echo pk.xxx > /data/lightpilot/mapbox_token`. Attiva `DisableUpdates`.
4. Nell'interfaccia: attiva *Lane Turn Desire* e controlla `LaneTurnValue`; il tetto di velocità frena solo con il
   longitudinale alpha attivo.

## Prova rapida

    PYTHONPATH=.:opendbc_repo python3 -m pytest openpilot/lightpilot/tests openpilot/selfdrive/navigation/tests -k "not EndToEnd"
